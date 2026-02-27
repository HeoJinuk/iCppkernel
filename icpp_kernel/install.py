import json
import os
import sys
import tempfile
from jupyter_client.kernelspec import KernelSpecManager

def main():
    kernel_json = {
        "argv": [sys.executable, "-m", "icpp_kernel.kernel", "-f", "{connection_file}"],
        "display_name": "Interactive C++ Kernel",
        "language": "c++",
        "interrupt_mode": "signal"
    }

    with tempfile.TemporaryDirectory() as td:
        os.chmod(td, 0o755)

        with open(os.path.join(td, 'kernel.json'), 'w') as f:
            json.dump(kernel_json, f, indent=4)

        kernel_spec_manager = KernelSpecManager()
        dest_dir = kernel_spec_manager.install_kernel_spec(
            source_dir=td,
            kernel_name="icpp_kernel",
            user=True,
            replace=True
        )

    print(f"✅ Interactive C++ Kernel installed successfully! (Path: {dest_dir})")

if __name__ == '__main__':
    main()
