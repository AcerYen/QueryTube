"""Create a NAS zip with forward-slash paths (Linux-safe)."""
import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: make-nas-zip.py <staging_dir> <output.zip>", file=sys.stderr)
        return 1

    staging = Path(sys.argv[1])
    zip_path = Path(sys.argv[2])

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(staging.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(staging).as_posix())

    print(f"created {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
