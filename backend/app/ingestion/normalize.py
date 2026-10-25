"""Text normalization (blueprint §7.2): fix encoding artefacts without changing meaning.

- Unicode NFC (not NFKC: compatibility folding would rewrite "²", "½" and full-width text).
- Typographic ligatures from PDF fonts are expanded ("ﬁ" → "fi").
- Invisible characters are removed, except ZWJ/ZWNJ, which change meaning in Bengali and other Indic
  scripts.
- Soft line wraps are joined and end-of-line hyphenation undone; bullets and numbered steps keep their line.
- Tables keep one row per line (`keep_lines=True`), so rows and cells are never merged.
"""

import re
import unicodedata

LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)
INVISIBLE = dict.fromkeys(
    map(ord, "\u00ad\u200b\u2060\ufeff")
)  # soft hyphen, zero-width space, word joiner, BOM
SPACES = dict.fromkeys(map(ord, "\u00a0\u2007\u202f\u3000\t"), " ") | dict.fromkeys(
    range(0x2000, 0x200B), " "
)

_BULLET = re.compile(r"^\s*[\u2022\u25aa\u25e6\u25cf\u2023\u2219]\s*")
_LIST_ITEM = re.compile(r"^\s*(?:-|\*|\d{1,3}[.)]|[a-z][.)])\s+")
_HYPHEN_WRAP = re.compile(r"(?<=[^\W\d_])-\n(?=[a-z])")
_MULTI_SPACE = re.compile(r" {2,}")


def normalize_text(text: str, *, keep_lines: bool = False) -> str:
    text = unicodedata.normalize("NFC", text).translate(LIGATURES).translate(INVISIBLE).translate(SPACES)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_BULLET.sub("- ", line).strip() for line in text.split("\n")]
    if keep_lines:
        return "\n".join(_MULTI_SPACE.sub(" ", line) for line in lines if line)

    text = _HYPHEN_WRAP.sub("", "\n".join(lines))
    out: list[str] = []
    for line in text.split("\n"):
        if not line:
            if out and out[-1] != "":
                out.append("")  # paragraph break
            continue
        if out and out[-1] and not _LIST_ITEM.match(line):
            out[-1] = f"{out[-1]} {line}"  # soft wrap: same paragraph
        else:
            out.append(line)
    return "\n".join(_MULTI_SPACE.sub(" ", line) for line in out).strip()
