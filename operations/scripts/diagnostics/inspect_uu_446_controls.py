"""Read-only evidence for the exact locally installed UU 4.46 service build."""
import argparse
import hashlib
from pathlib import Path
import struct
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "tools/uu-readonly-inspection-20261103"))
    import capstone
    import pefile

    service = Path(r"C:\Program Files\Netease\GameViewer\GameViewerService.exe")
    digest = hashlib.sha256(service.read_bytes()).hexdigest()
    if digest != "23f67859b7ac897198cf4824c885a94cfb81120229e1e16fe7865b286b431366":
        raise SystemExit("UNINSPECTED_BUILD: no version-specific table offsets were used")
    image = pefile.PE(str(service))
    base = image.OPTIONAL_HEADER.ImageBase
    lines = [f"Service: {service}", "Version: 4.46.0.4716", f"SHA256: {digest}",
             "Read-only static inspection; not vendor source or a live control test."]
    targets = {}
    for control in (1, 5, 14, 15, 130, 131, 132, 133):
        index = image.get_data(0xE04C8 + control - 1, 1)[0]
        target = base + struct.unpack("<I", image.get_data(0xE04B4 + index * 4, 4))[0]
        targets[control] = target
        lines.append(f"CONTROL {control}: {target:#x}")
    if targets[130] != base + 0xE034A or targets[131] != base + 0xE03D9:
        raise SystemExit("CONTROL_MAP_MISMATCH")
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    for instruction in decoder.disasm(image.get_data(0xE01B0, 0xE04B3 - 0xE01B0), base + 0xE01B0):
        lines.append(f"{instruction.address:016x} {instruction.mnemonic:8} {instruction.op_str}")
    lines.extend([
        "Reviewed: pause branch selects byte 0 at e03cc; resume selects byte 1 at e045b.",
        "Both converge at e0463 and exchange the supervision byte at object offset 0x29.",
        "The runtime script retains its pinned service hash and worker identity checks.",
    ])
    # Create-only output preserves all historical evidence.
    with args.out.open("x", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")
    print(f"UU_446_CONTROL_MAP_INSPECTED: {args.out}")


if __name__ == "__main__":
    main()
