# 从 proto 生成 Python gRPC 桩（controller_pb2.py / controller_pb2_grpc.py）
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PROTO = REPO / "proto" / "controller.proto"
OUT = REPO / "orchestrator" / "generated"

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "__init__.py").write_text("", encoding="utf-8")

from grpc_tools import protoc  # noqa: E402

args = [
    "grpc_tools.protoc",
    f"-I{REPO / 'proto'}",
    f"--python_out={OUT}",
    f"--grpc_python_out={OUT}",
    str(PROTO),
]
code = protoc.main(args)
if code != 0:
    sys.exit(f"protoc failed with code {code}")
print(f"[stubs] generated -> {OUT}")
