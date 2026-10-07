"""Export the generated brand asset in the sizes required by Windows and Tk."""
from pathlib import Path

from PIL import Image


def main():
    assets = Path(__file__).resolve().parent
    with Image.open(assets / 'app-icon-source.png') as source:
        icon = source.convert('RGBA')
        if icon.width != icon.height:
            raise ValueError('The original icon must be square')
        for size in (32, 64):
            icon.resize((size, size), Image.Resampling.LANCZOS).save(assets / f'app-icon-{size}.png')
        icon.save(assets / 'app.ico', sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)])


if __name__ == '__main__':
    main()
