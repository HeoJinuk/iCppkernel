# iCppKernel - Interactive C++ Kernel for Jupyter

iCkernel의 C++ 버전입니다. `cin` 입력, 무한루프 중단, 컴파일 에러 컬러링을 지원합니다.

## 요구사항

- Python 3.8+
- g++ (GCC C++ 컴파일러)
- Jupyter Notebook

## 설치

```bash
# 1. 패키지 설치
pip install .

# 2. 커널 등록
install-icpp-kernel
```

## 사용법

Jupyter에서 **Interactive C++ Kernel** 선택 후 C++17 코드 작성.

### 예제 1 - cin 입력

```cpp
#include <iostream>
using namespace std;

int main() {
    int n;
    cout << "숫자를 입력하세요: ";
    cin >> n;
    cout << "입력값: " << n << endl;
    return 0;
}
```

### 예제 2 - //%cflags 매직 커맨드

```cpp
//%cflags -O2
#include <iostream>
#include <vector>
#include <algorithm>
using namespace std;

int main() {
    vector<int> v = {5, 3, 1, 4, 2};
    sort(v.begin(), v.end());
    for (int x : v) cout << x << " ";
    return 0;
}
```

### 예제 3 - getline

```cpp
#include <iostream>
#include <string>
using namespace std;

int main() {
    string name;
    cout << "이름을 입력하세요: ";
    getline(cin, name);
    cout << "안녕하세요, " << name << "!" << endl;
    return 0;
}
```

## 기능

| 기능 | 설명 |
|------|------|
| `cin >>` 입력 지원 | 입력 시 Jupyter input box 자동 표시 |
| `getline` 지원 | 한 줄 입력도 input box로 처리 |
| 무한루프 중단 | ⏹ Stop 버튼으로 즉시 종료 |
| 에러 컬러링 | 컴파일 에러 빨강, 경고 노랑, 노트 파랑 |
| `//%cflags` | 추가 컴파일 옵션 지정 (예: `-O2`, `-lm`) |
| C++17 기본 | `std::optional`, structured bindings 등 사용 가능 |
| 크로스 플랫폼 | Windows / macOS / Linux 모두 지원 |
