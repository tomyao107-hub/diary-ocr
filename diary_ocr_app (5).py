"""Compatibility import for scripts that used the pre-1.0 filename."""

from diary_ocr import editor as _editor


globals().update(
    {
        name: value
        for name, value in vars(_editor).items()
        if not name.startswith("__")
    }
)


if __name__ == "__main__":
    main()
