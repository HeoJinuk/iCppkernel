from ipykernel.kernelbase import Kernel
import subprocess
import os
import threading
import queue
import uuid
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse
import shutil
import tempfile
import platform
import signal
import re

# ==========================================
# 1. Configuration & Templates
# ==========================================

CPP_BOOTSTRAP_CODE = r"""
#include <cstdio>
#include <cstdlib>

#ifdef _WIN32
    #include <windows.h>
    void _init_os() { SetConsoleOutputCP(65001); }
#else
    void _init_os() {}
#endif

/* 초기화: 버퍼링 끄기 및 한글 설정 */
__attribute__((constructor))
static void _init_jupyter() {
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    setvbuf(stdin,  NULL, _IONBF, 0);
    _init_os();
}

/* 입력 트리거 함수: 파이썬에게 입력창 띄우라고 신호 보냄 */
static void _trigger_input() {
    printf("<<__REQ__>>");
    fflush(stdout);
}

/*
 * cin 후킹: cin의 operator>> 및 getline을 감싸는 헬퍼 함수들
 * C에서의 #define 방식 대신, C++에서는 네임스페이스 래퍼를 사용합니다.
 */
#include <iostream>
#include <string>
#include <sstream>

namespace _icpp_io {

/* cin >> val 대체 */
template<typename T>
std::istream& read(T& val) {
    _trigger_input();
    return std::cin >> val;
}

/* getline 대체 */
inline std::istream& getline_s(std::istream& is, std::string& s) {
    _trigger_input();
    return std::getline(is, s);
}
inline std::istream& getline_s(std::istream& is, std::string& s, char delim) {
    _trigger_input();
    return std::getline(is, s, delim);
}

} // namespace _icpp_io

/* 매크로로 cin >> 와 getline 을 후킹 */
#define cin  ([](auto& _s) -> std::istream& { return _s; }(std::cin)) // cin 참조 유지용 dummy
#undef  cin
/* operator>> 후킹: cin >> x  →  _icpp_io::read(x) */
namespace std {
    struct _IcppCin {
        template<typename T>
        _IcppCin& operator>>(T& val) {
            _trigger_input();
            std::cin >> val;
            return *this;
        }
        // ignore, peek 등은 원본 cin에 위임
        std::streamsize gcount() const { return std::cin.gcount(); }
    };
}

/* getline 후킹 */
#define getline(is, ...) _icpp_io::getline_s(is, __VA_ARGS__)

/* scanf 계열도 C 코드 혼용을 위해 유지 */
#define scanf(...) (_trigger_input(), scanf(__VA_ARGS__))
#define getchar()  (_trigger_input(), getchar())
#define fgets(s, n, stream) ((stream) == stdin ? (_trigger_input(), fgets(s, n, stream)) : fgets(s, n, stream))
"""

# cin >> 후킹은 매크로보다 전역 객체 교체가 더 안정적입니다.
# 위 방식 대신 아래처럼 단순하게 처리합니다 (주석 처리된 복잡한 방식 대신).
CPP_BOOTSTRAP_CODE = r"""
#ifdef _WIN32
    #include <windows.h>
    namespace { struct _OsInit { _OsInit() { SetConsoleOutputCP(65001); } } _os_init; }
#endif

#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>

/* 버퍼링 끄기 */
namespace {
    struct _JupyterInit {
        _JupyterInit() {
            setvbuf(stdout, NULL, _IONBF, 0);
            setvbuf(stderr, NULL, _IONBF, 0);
            setvbuf(stdin,  NULL, _IONBF, 0);
            std::ios::sync_with_stdio(true);
            std::cin.tie(nullptr);
        }
    } _jupyter_init;
}

/* 입력 트리거: Python에게 입력창 띄우라고 신호 */
inline void _trigger_input() {
    printf("<<__REQ__>>");
    fflush(stdout);
}

/* cin 후킹용 래퍼 스트림 */
struct _IcppCinWrapper {
    template<typename T>
    _IcppCinWrapper& operator>>(T& val) {
        _trigger_input();
        std::cin >> val;
        return *this;
    }
    // 나머지 cin 멤버 위임
    operator std::istream&() { return std::cin; }
    bool fail()  const { return std::cin.fail();  }
    bool eof()   const { return std::cin.eof();   }
    bool good()  const { return std::cin.good();  }
    explicit operator bool() const { return (bool)std::cin; }
    void ignore(std::streamsize n = 1, int delim = EOF) { std::cin.ignore(n, delim); }
    int  peek()  { return std::cin.peek();  }
    int  get()   { _trigger_input(); return std::cin.get(); }
    std::istream& getline(char* s, std::streamsize n) {
        _trigger_input();
        return std::cin.getline(s, n);
    }
};

/* getline 후킹 */
inline std::istream& _icpp_getline(std::istream& is, std::string& s) {
    _trigger_input();
    return std::getline(is, s);
}
inline std::istream& _icpp_getline(std::istream& is, std::string& s, char d) {
    _trigger_input();
    return std::getline(is, s, d);
}

/* 전역 cin 교체 및 getline 매크로 */
static _IcppCinWrapper icpp_cin;
#define cin     icpp_cin
#define getline(is, ...) _icpp_getline(is, __VA_ARGS__)

/* C 스타일 입력도 후킹 (혼용 대비) */
#define scanf(...)          (_trigger_input(), scanf(__VA_ARGS__))
#define getchar()           (_trigger_input(), getchar())
#define fgets(s, n, stream) ((stream) == stdin ? (_trigger_input(), fgets(s, n, stream)) : fgets(s, n, stream))
"""

INPUT_HTML_TEMPLATE = """
<div class="lm-Widget jp-Stdin jp-OutputArea-output">
    <div class="lm-Widget jp-InputArea jp-Stdin-inputWrapper">
        <input type="text" id="box-{req_id}" class="jp-Stdin-input" autocomplete="off" placeholder="">
    </div>
    <script>
        (function() {{
            var box = document.getElementById("box-{req_id}");
            setTimeout(function() {{ box.focus(); }}, 50);
            box.addEventListener("keydown", function(e) {{
                if (e.key === "Enter") {{
                    e.preventDefault();
                    var val = box.value;
                    box.disabled = true;
                    fetch("http://localhost:{port}", {{
                        method: "POST", headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
                        body: "id={req_id}&value=" + encodeURIComponent(val)
                    }}).catch(console.error);
                }}
            }});
        }})();
    </script>
</div>
"""

# ==========================================
# 2. Input Server Manager (iCkernel과 동일 구조)
# ==========================================

class ServerState:
    data = {}
    event = threading.Event()

class RequestHandler(BaseHTTPRequestHandler):
    def _set_headers(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            parsed = urllib.parse.parse_qs(post_data)
            req_id = parsed.get('id', [None])[0]
            value  = parsed.get('value', [''])[0]
            if req_id:
                ServerState.data[req_id] = value
                ServerState.event.set()
            self._set_headers()
            self.wfile.write(b"OK")
        except:
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args): pass

class InputServer:
    def __init__(self):
        self.port = self._find_free_port()
        self.server = HTTPServer(('localhost', self.port), RequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _find_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', 0))
            return s.getsockname()[1]

    def wait_for_input(self, req_id):
        ServerState.event.clear()
        while True:
            is_set = ServerState.event.wait(timeout=0.1)
            if is_set and req_id in ServerState.data:
                return ServerState.data.pop(req_id)

    def get_port(self):
        return self.port

# ==========================================
# 3. Main Kernel Class
# ==========================================

class ICppKernel(Kernel):
    implementation = 'ICppKernel'
    implementation_version = '1.0'
    language = 'c++'
    language_version = 'C++17'
    banner = "iCppKernel v1.0 - Interactive C++17 Kernel"
    language_info = {
        'name': 'c++',
        'mimetype': 'text/x-c++src',
        'file_extension': '.cpp',
        'pygments_lexer': 'cpp',
        'codemirror_mode': 'text/x-c++src',
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.input_server = InputServer()
        self.cell_output_buffer = ""
        self.current_process = None
        self.is_windows = (platform.system() == 'Windows')
        self.safe_base_dir = self._ensure_safe_dir()

    def _ensure_safe_dir(self):
        """한글 경로 문제 없는 안전한 임시 디렉토리 확보"""
        if self.is_windows:
            public_path = os.environ.get('PUBLIC', r'C:\Users\Public')
            target_dir = os.path.join(public_path, 'icpp_workspace')
            try:
                os.makedirs(target_dir, exist_ok=True)
                return target_dir
            except:
                target_dir = r'C:\icpp_temp'
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    return target_dir
                except:
                    return tempfile.gettempdir()
        else:
            base = os.environ.get('TMPDIR', '/tmp')
            target_dir = os.path.join(base, 'icpp_workspace')
            try:
                os.makedirs(target_dir, exist_ok=True)
                return target_dir
            except:
                return tempfile.gettempdir()

    # ──────────────────────────────────────────────
    # Execute Entry Point
    # ──────────────────────────────────────────────

    def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=True):
        self.cell_output_buffer = ""
        self.build_dir = tempfile.mkdtemp(dir=self.safe_base_dir)

        exe_name = 'source.exe' if self.is_windows else 'source'
        src_file = os.path.join(self.build_dir, 'source.cpp')
        exe_file = os.path.join(self.build_dir, exe_name)

        try:
            if self._compile_code(code, src_file, exe_file):
                self._run_process(exe_file)

            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {},
            }

        except KeyboardInterrupt:
            self._kill_process()
            self._print_stream("\n\033[31m> 사용자에 의해 실행이 중단되었습니다.\033[0m\n")
            self.send_response(self.iopub_socket, 'clear_output', {'wait': True})
            return {'status': 'abort', 'execution_count': self.execution_count}

        finally:
            self._kill_process()
            self._cleanup()

    # ──────────────────────────────────────────────
    # Compile
    # ──────────────────────────────────────────────

    def _parse_cflags(self, code: str) -> list:
        """코드에서 //%cflags 매직 커맨드를 파싱해 추가 컴파일 플래그를 반환"""
        flags = []
        for line in code.splitlines():
            if line.strip().startswith("//%cflags"):
                options = line.replace("//%cflags", "").strip().split()
                flags.extend(options)
        return flags

    def _compile_code(self, code, src_file, exe_file):
        extra_args = self._parse_cflags(code)

        # 부트스트랩 코드를 사용자 코드 앞에 주입
        full_code = CPP_BOOTSTRAP_CODE + "\n" + code

        with open(src_file, 'w', encoding='utf-8') as f:
            f.write(full_code)

        try:
            src_name = os.path.basename(src_file)
            exe_name = os.path.basename(exe_file)
            cmd = [
                'g++',
                '-std=c++17',
                src_name,
                '-o', exe_name,
                '-fexec-charset=UTF-8',
            ] + extra_args

            env = os.environ.copy()
            env['TEMP']   = self.build_dir
            env['TMP']    = self.build_dir
            env['TMPDIR'] = self.build_dir

            subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                cwd=self.build_dir,
                env=env,
            )
            return True

        except subprocess.CalledProcessError as e:
            output_str = e.output.decode('utf-8', errors='replace')
            # 부트스트랩 코드로 인한 줄 번호 오프셋 보정
            output_str = self._adjust_line_numbers(output_str)
            self._print_stream(self._colorize_gpp_output(output_str))
            return False

        except FileNotFoundError:
            self._print_stream(
                "\033[1;31mError: g++ not found.\033[0m\n"
                "Please install GCC/G++ and add it to your PATH.\n"
                "  - Windows: https://winlibs.com\n"
                "  - macOS:   xcode-select --install\n"
                "  - Linux:   sudo apt install build-essential\n"
            )
            return False

    def _adjust_line_numbers(self, text: str) -> str:
        """
        부트스트랩 코드가 앞에 삽입되어 줄 번호가 밀리는 문제를 보정합니다.
        부트스트랩 코드의 줄 수를 세어 에러 메시지의 줄 번호에서 뺍니다.
        """
        offset = CPP_BOOTSTRAP_CODE.count('\n') + 1  # +1 for the separator newline

        def replace_lineno(m):
            lineno = int(m.group(1))
            adjusted = max(1, lineno - offset)
            return m.group(0).replace(m.group(1), str(adjusted), 1)

        # "source.cpp:줄번호:열번호:" 패턴 보정
        return re.sub(r'source\.cpp:(\d+):', replace_lineno, text)

    def _colorize_gpp_output(self, text: str) -> str:
        """컴파일 에러/경고에 색상 적용 (iCkernel과 동일 방식)"""
        text = re.sub(r'(error:.*)',   r'\033[1;31m\1\033[0m', text)
        text = re.sub(r'(warning:.*)', r'\033[1;33m\1\033[0m', text)
        text = re.sub(r'(note:.*)',    r'\033[1;36m\1\033[0m', text)
        text = re.sub(r'(source\.cpp:\d+:\d+:)', r'\033[1m\1\033[0m', text)
        return text

    # ──────────────────────────────────────────────
    # Run
    # ──────────────────────────────────────────────

    def _run_process(self, exe_file):
        if not os.path.exists(exe_file):
            return

        if not self.is_windows:
            try:
                st = os.stat(exe_file)
                os.chmod(exe_file, st.st_mode | 0o111)
            except:
                pass

        env = os.environ.copy()
        env['TEMP']   = self.build_dir
        env['TMP']    = self.build_dir
        env['TMPDIR'] = self.build_dir

        run_cmd = [exe_file]
        if not self.is_windows and not exe_file.startswith('/'):
            run_cmd = ['./' + os.path.basename(exe_file)]

        self.current_process = subprocess.Popen(
            run_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=0,
            cwd=self.build_dir,
            env=env,
        )

        q = queue.Queue()

        def reader_thread(proc, out_q):
            while True:
                try:
                    char = proc.stdout.read(1)
                except (ValueError, OSError):
                    break
                if not char:
                    break
                out_q.put(char)
            try:
                proc.stdout.close()
            except:
                pass

        t = threading.Thread(target=reader_thread, args=(self.current_process, q), daemon=True)
        t.start()

        output_chunk = ""
        req_marker = "<<__REQ__>>"

        while t.is_alive() or not q.empty():
            try:
                char = q.get(timeout=0.05)
                output_chunk += char

                if req_marker in output_chunk:
                    pre_text = output_chunk.replace(req_marker, "")
                    self._print_stream(pre_text)
                    output_chunk = ""

                    user_input = self._handle_input_request()

                    try:
                        if self.current_process and self.current_process.stdin:
                            self.current_process.stdin.write(user_input + "\n")
                            self.current_process.stdin.flush()
                    except OSError:
                        pass

                elif char == '\n' or len(output_chunk) > 200:
                    self._print_stream(output_chunk)
                    output_chunk = ""

            except queue.Empty:
                if self.current_process.poll() is not None and q.empty():
                    break
                continue

        if output_chunk:
            self._print_stream(output_chunk)

    # ──────────────────────────────────────────────
    # Input Handling
    # ──────────────────────────────────────────────

    def _handle_input_request(self):
        req_id = str(uuid.uuid4())
        self._display_html_input(req_id)

        user_input = self.input_server.wait_for_input(req_id)
        if user_input is None:
            user_input = ""

        self.send_response(self.iopub_socket, 'clear_output', {'wait': True})

        prefix = "\n" if self.cell_output_buffer and not self.cell_output_buffer.endswith('\n') else ""
        self._print_stream(f"{prefix}{user_input}\n")

        return user_input

    def _display_html_input(self, req_id):
        html = INPUT_HTML_TEMPLATE.format(req_id=req_id, port=self.input_server.get_port())
        self.send_response(
            self.iopub_socket, 'display_data',
            {'data': {'text/html': html}, 'metadata': {}},
        )

    # ──────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────

    def _print_stream(self, text: str):
        self.cell_output_buffer += text
        self.send_response(self.iopub_socket, 'stream', {'name': 'stdout', 'text': text})

    def _kill_process(self):
        if self.current_process:
            try:
                self.current_process.terminate()
                try:
                    self.current_process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    if self.is_windows:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(self.current_process.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    else:
                        os.kill(self.current_process.pid, signal.SIGKILL)
            except:
                pass
            finally:
                self.current_process = None

    def _cleanup(self):
        if hasattr(self, 'build_dir') and os.path.exists(self.build_dir):
            shutil.rmtree(self.build_dir, ignore_errors=True)


if __name__ == '__main__':
    from ipykernel.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=ICppKernel)
