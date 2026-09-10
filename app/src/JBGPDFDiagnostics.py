"""Why a scanned report yields no text.

A document that OCR cannot read is skipped with the message "gav bara N
tecken text". That is true and useless: it says the pipeline failed without
saying whether the scan was unreadable, the pages rendered blank, or OCR was
never asked to look at them. Answering that by hand meant running two
throwaway scripts against the original file, which is exactly the sort of
knowledge that lives in someone's shell history and nowhere else.

So the checks run in the pipeline instead, at the only moment they matter:
when OCR has already been tried and came back empty.

Two levels, because they cost differently.

* :func:`profile` reads the page dictionary. No rendering, microseconds per
  page. Logged as one line whenever a document is about to be OCR-ed, so a
  run always records what the scans looked like going in.
* :func:`diagnose` additionally rasterises a few pages to answer the one
  question the dictionary cannot: is there ink on the page at all. Only runs
  when OCR has already failed.

Rendering is done in memory. Writing the PNGs out is more convenient for a
human but puts unmasked pages of a scanned report on disk as images, which
is the thing the rest of this pipeline goes to some trouble to avoid, so it
is off unless asked for.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

# Below this fraction of non-white pixels a rendered page is blank. Not zero:
# a scan border or a stray speck should not count as content.
BLANK_INK_FRACTION = 0.001
# Sampling every 40th pixel is ample for "is this page blank", and keeps a
# full-page render at 150 dpi to a few hundred thousand comparisons.
INK_SAMPLE_STRIDE = 40
RENDER_DPI = 150
PAGES_TO_RENDER = 3
# Tesseract's accuracy falls off below roughly this, and digits suffer first.
LOW_DPI_THRESHOLD = 200

# Verdicts. The point of naming them is that the caller can branch on the
# cause rather than parse a sentence.
VERDICT_BLANK_RENDER = "tom_rendering"
VERDICT_NO_IMAGES = "inga_bilder"
VERDICT_INLINE_IMAGES = "inbaddade_bilder"
VERDICT_LOW_RESOLUTION = "lag_upplosning"
VERDICT_HAS_TEXT = "text_finns"
VERDICT_READABLE = "lasbar"
VERDICT_UNKNOWN = "okand"

_EXPLANATIONS = {
    VERDICT_BLANK_RENDER: (
        "Sidorna renderar tomma. Det som syns i en pdf-läsare når varken "
        "pymupdf eller ghostscript, så OCR har fått blanka sidor att läsa. "
        "Filen är strukturellt trasig snarare än svårläst och kan inte "
        "räddas med andra OCR-inställningar."
    ),
    VERDICT_NO_IMAGES: (
        "Sidorna innehåller ingen inbäddad bild men ritas ändå upp. "
        "Innehållet är då vektorgrafik eller inbäddade bilder i "
        "innehållsströmmen, som get_images() inte listar. OCR måste tvingas "
        "(force_ocr) för att rastrera sidan i stället för att leta efter "
        "bilder att tolka."
    ),
    VERDICT_INLINE_IMAGES: (
        "Sidbilderna ligger inbäddade i innehållsströmmen i stället för som "
        "resurser. De renderar korrekt men syns inte för verktyg som söker "
        "efter bildobjekt. Tvingad OCR rastrerar sidan och kommer åt dem."
    ),
    VERDICT_LOW_RESOLUTION: (
        "Skanningen är lågupplöst. Tesseract läser brödtext dåligt och "
        "siffror sämre under omkring 200 dpi, och en årsredovisning består "
        "till stor del av siffror."
    ),
    VERDICT_HAS_TEXT: (
        "Sidorna har redan ett textlager. Med skip_text hoppar OCR över hela "
        "sidan så snart den innehåller någon text alls, även en stämpel "
        "eller en digital signatur."
    ),
    VERDICT_READABLE: (
        "Sidorna innehåller bilder i rimlig upplösning och renderar med "
        "innehåll. Det finns något att läsa, så felet ligger i OCR-anropet "
        "snarare än i dokumentet."
    ),
    VERDICT_UNKNOWN: "Ingen entydig orsak kunde fastställas.",
}


@dataclass
class PageProfile:
    number: int
    rotation: int = 0
    chars: int = 0
    images: int = 0
    displayed_images: int = 0
    inline_images: int = 0
    min_dpi: int | None = None
    rendered: bool = False
    ink_fraction: float | None = None


@dataclass
class Diagnosis:
    path: Path
    page_count: int = 0
    producer: str = ""
    pages: list[PageProfile] = field(default_factory=list)
    verdict: str = VERDICT_UNKNOWN
    rendered_pages: list[Path] = field(default_factory=list)
    error: str = ""

    @property
    def total_chars(self) -> int:
        return sum(page.chars for page in self.pages)

    @property
    def pages_with_text(self) -> int:
        return sum(1 for page in self.pages if page.chars)

    @property
    def pages_with_images(self) -> int:
        return sum(1 for page in self.pages if page.images or page.displayed_images)

    @property
    def explanation(self) -> str:
        return _EXPLANATIONS.get(self.verdict, _EXPLANATIONS[VERDICT_UNKNOWN])

    def one_line(self) -> str:
        """The profile in a single log line."""
        if self.error:
            return f"Kunde inte profilera {self.path.name}: {self.error}"
        dpis = [p.min_dpi for p in self.pages if p.min_dpi]
        resolution = f"{min(dpis)}–{max(dpis)} dpi" if dpis else "inga bilder"
        rotations = sorted({p.rotation for p in self.pages})
        return (
            f"Dokumentprofil {self.path.name}: {self.page_count} sidor, "
            f"{self.pages_with_text} med textlager ({self.total_chars} tecken), "
            f"{self.pages_with_images} med bild, {resolution}, "
            f"rotation {rotations}, producer {self.producer or 'okänd'}."
        )

    def report(self) -> list[str]:
        """The full finding, one list entry per log line."""
        if self.error:
            return [f"Kunde inte undersöka {self.path.name}: {self.error}"]

        lines = [self.one_line()]
        for page in self.pages:
            if not page.rendered:
                continue
            ink = page.ink_fraction or 0.0
            state = "TOM" if ink < BLANK_INK_FRACTION else "innehåll"
            lines.append(
                f"  Sida {page.number}: {page.chars} tecken, "
                f"{page.images} bildobjekt, {page.inline_images} inbäddade, "
                f"rendering {ink * 100:.2f}% mörka pixlar -> {state}"
            )
        for saved in self.rendered_pages:
            lines.append(f"  Renderad sida sparad: {saved}")
        lines.append(f"Slutsats ({self.verdict}): {self.explanation}")
        return lines


def _count_inline_images(page) -> int:
    """Images drawn straight into the content stream with BI ... ID ... EI.

    get_images() lists resources, and an inline image is not one, so a page
    built this way looks empty to every image-counting check while rendering
    perfectly in a viewer.
    """
    try:
        raw = page.read_contents()
    except Exception:
        return 0
    return len(re.findall(rb"(?:^|\s)BI\s", raw))


def _page_min_dpi(page) -> int | None:
    """Lowest effective resolution among the images drawn on the page."""
    width_inches = page.rect.width / 72.0
    height_inches = page.rect.height / 72.0
    if width_inches <= 0 or height_inches <= 0:
        return None

    best: int | None = None
    for image in page.get_images(full=True):
        pixel_width, pixel_height = image[2], image[3]
        if not pixel_width or not pixel_height:
            continue
        dpi = min(pixel_width / width_inches, pixel_height / height_inches)
        best = round(dpi) if best is None else min(best, round(dpi))
    return best


def _ink_fraction(pixmap) -> float:
    """Fraction of sampled pixels that are not near-white."""
    samples = pixmap.samples
    channels = pixmap.n
    step = channels * INK_SAMPLE_STRIDE
    if step <= 0 or len(samples) < channels:
        return 0.0
    dark = 0
    total = 0
    for offset in range(0, len(samples) - channels, step):
        total += 1
        if samples[offset] < 200:
            dark += 1
    return (dark / total) if total else 0.0


def profile(pdf_path) -> Diagnosis:
    """Read what the page dictionary says, without rendering anything."""
    pdf_path = Path(pdf_path)
    result = Diagnosis(path=pdf_path)
    try:
        with pymupdf.open(pdf_path) as doc:
            result.page_count = doc.page_count
            result.producer = (doc.metadata or {}).get("producer") or ""
            for index, page in enumerate(doc, start=1):
                result.pages.append(
                    PageProfile(
                        number=index,
                        rotation=page.rotation,
                        chars=len(page.get_text().strip()),
                        images=len(page.get_images(full=True)),
                        displayed_images=len(page.get_image_info()),
                        inline_images=_count_inline_images(page),
                        min_dpi=_page_min_dpi(page),
                    )
                )
    except Exception as ex:  # a diagnosis must never be the thing that crashes
        result.error = str(ex)
    return result


def _choose_verdict(result: Diagnosis, rendered_any: bool) -> str:
    rendered = [p for p in result.pages if p.rendered]
    if rendered_any and rendered:
        if all((p.ink_fraction or 0.0) < BLANK_INK_FRACTION for p in rendered):
            return VERDICT_BLANK_RENDER

    if any(p.inline_images for p in result.pages):
        return VERDICT_INLINE_IMAGES

    blank_pages = [p for p in result.pages if not p.chars]
    if blank_pages and not any(p.images or p.displayed_images for p in blank_pages):
        return VERDICT_NO_IMAGES

    dpis = [p.min_dpi for p in result.pages if p.min_dpi]
    if dpis and min(dpis) < LOW_DPI_THRESHOLD:
        return VERDICT_LOW_RESOLUTION

    if result.pages_with_text:
        return VERDICT_HAS_TEXT

    if result.pages_with_images:
        return VERDICT_READABLE

    return VERDICT_UNKNOWN


def diagnose(pdf_path, render_pages: int = PAGES_TO_RENDER, save_to=None) -> Diagnosis:
    """Profile the document and rasterise a few pages to see if they are blank.

    ``save_to`` writes the renders out as PNG for a human to look at. Leave it
    unset in normal operation: the renders are unmasked pages of a scanned
    annual report.
    """
    result = profile(pdf_path)
    if result.error:
        return result

    # Prefer pages with no text of their own; those are the ones OCR was
    # supposed to handle and the ones whose emptiness would explain the run.
    candidates = [p for p in result.pages if not p.chars] or result.pages
    targets = {p.number for p in candidates[:render_pages]}

    rendered_any = False
    try:
        zoom = RENDER_DPI / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        with pymupdf.open(result.path) as doc:
            for page_profile in result.pages:
                if page_profile.number not in targets:
                    continue
                try:
                    pixmap = doc[page_profile.number - 1].get_pixmap(matrix=matrix)
                except Exception as ex:
                    logger.debug(f"Kunde inte rendera sida {page_profile.number}: {ex}")
                    continue
                page_profile.rendered = True
                page_profile.ink_fraction = _ink_fraction(pixmap)
                rendered_any = True
                if save_to:
                    target = Path(save_to) / (
                        f"{result.path.stem}_diagnos_sida_{page_profile.number:02d}.png"
                    )
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        pixmap.save(target)
                        result.rendered_pages.append(target)
                    except Exception as ex:
                        logger.debug(f"Kunde inte spara {target}: {ex}")
    except Exception as ex:
        result.error = str(ex)
        return result

    result.verdict = _choose_verdict(result, rendered_any)
    return result


def log_report(result: Diagnosis, log=logger, level: int = logging.WARNING) -> None:
    for line in result.report():
        log.log(level, line)
