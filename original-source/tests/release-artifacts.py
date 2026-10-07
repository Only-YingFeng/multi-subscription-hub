"""Verify the built public release and create a whitelist-only distribution ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

import pefile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_layout import SOURCE_FILES, licensed_files


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', required=True)
    parser.add_argument('--zip', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    release = Path(args.release).resolve()
    archive = Path(args.zip).resolve()
    if archive.exists():
        raise RuntimeError('Distribution ZIP already exists; preserve the previous build')
    files = [release / 'ClashZeroOmega.exe', release / '使用说明.md']
    files += [release / '第三方许可' / name for name in ('Node_LICENSE.txt', 'Python_LICENSE.txt', 'PyInstaller_LICENSE.txt', 'PyYAML_LICENSE.txt')]
    source = Path(__file__).resolve().parents[1]
    ui_license_files = licensed_files(source)
    if any((release / '第三方许可' / 'UI' / path.relative_to(source / 'licenses-ui')).read_bytes()
           != path.read_bytes() for path in ui_license_files):
        raise RuntimeError('Release UI licenses differ from the reviewed source')
    files += [release / '第三方许可' / 'UI' / path.relative_to(source / 'licenses-ui')
              for path in ui_license_files]
    for name in SOURCE_FILES:
        original, target = source / name, release / '源码' / name
        if not original.is_file() or target.exists():
            raise RuntimeError('Source release baseline is missing or already exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
        files.append(target)
    for original in ui_license_files:
        target = release / '源码' / 'licenses-ui' / original.relative_to(source / 'licenses-ui')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
        files.append(target)
    expected_files = {path.resolve() for path in files}
    actual_files = {path.resolve() for path in release.rglob('*') if path.is_file()}
    if actual_files != expected_files or not all(path.is_file() for path in files):
        raise RuntimeError('Release contains unexpected or missing files')
    executable = files[0].read_bytes()
    icon_path = Path(__file__).resolve().parents[1] / 'assets' / 'app.ico'
    icon = icon_path.read_bytes()
    count = struct.unpack_from('<HHH', icon)[2]
    source_icons = []
    for index in range(count):
        length, offset = struct.unpack_from('<II', icon, 6 + 16 * index + 8)
        source_icons.append(digest(icon[offset:offset + length]))
    binary = pefile.PE(data=executable)
    embedded_icons = []
    for resource in binary.DIRECTORY_ENTRY_RESOURCE.entries:
        if resource.id == 3:
            for entry in resource.directory.entries:
                for language in entry.directory.entries:
                    data = language.data.struct
                    embedded_icons.append(digest(binary.get_data(data.OffsetToData, data.Size)))
    if sorted(source_icons) != sorted(embedded_icons):
        raise RuntimeError('Native EXE icon resources differ from the final icon')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            bundle.write(path, arcname=path.relative_to(release).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or bundle.read('ClashZeroOmega.exe') != executable:
            raise RuntimeError('Distribution archive validation failed')
        members = bundle.namelist()
    report = {'releaseFiles': members, 'releaseFileCount': len(files), 'nativeIconResourcesMatch': True,
              'nativeIconSizeCount': count, 'zipCrcPassed': True, 'zipExeMatches': True,
              'privateUserDataIncluded': False, 'rebuildSourceIncluded': True,
              'exeSha256': digest(executable), 'zipSha256': digest(archive.read_bytes()),
              'exeBytes': len(executable), 'zipBytes': archive.stat().st_size}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
