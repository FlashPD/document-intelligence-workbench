"""Join disjoint OCR fragments on one visual line without inventing evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .contracts import Box, DocumentPage, FieldValue, TextSpan


@dataclass(frozen=True)
class SpatialLine:
    spans: tuple[TextSpan, ...]

    @property
    def text(self) -> str:
        return " ".join(span.text.strip() for span in self.spans)

    def observed(self, match: re.Match, group: int = 1) -> FieldValue:
        """Cite only fragments intersecting this captured value's text range."""
        start, end = match.span(group)
        offset = 0
        sources = []
        for span in self.spans:
            length = len(span.text.strip())
            if offset < end and offset + length > start:
                sources.append(span)
            offset += length + 1
        return FieldValue(match.group(group).strip(), " ".join(span.text for span in sources),
                          tuple(span.id for span in sources))


def _view_spans(page: DocumentPage) -> tuple[TextSpan, ...]:
    """Use explicit split table headers to orient grouping on sideways rasters.

    This is an extraction-only view: stored OCR boxes and the raster stay intact.
    Without both anchors, leave geometry unchanged rather than guess direction.
    """
    descriptions = [s for s in page.spans if s.box and s.text.strip().casefold() == "description"]
    amounts = [s for s in page.spans if s.box and s.text.strip().casefold() in ("amount", "line total")]
    if len(descriptions) != 1 or len(amounts) != 1:
        return page.spans
    description, amount = descriptions[0].box, amounts[0].box
    assert description and amount
    if ((description.right - description.left) * page.width_px >=
            (description.bottom - description.top) * page.height_px):
        return page.spans
    if abs(description.left - amount.left) > .02:
        return page.spans
    clockwise = description.top > amount.top

    def transform(box: Box) -> Box:
        if clockwise:
            return Box(1 - box.bottom, box.left, 1 - box.top, box.right)
        return Box(box.top, 1 - box.right, box.bottom, 1 - box.left)

    return tuple(replace(s, box=transform(s.box)) if s.box else s for s in page.spans)


def _same_line(left: TextSpan, right: TextSpan) -> bool:
    assert left.box and right.box
    a, b = left.box, right.box
    if a.left < b.right and b.left < a.right:
        return False  # Overlapping boxes are not distinct columns.
    overlap = min(a.bottom, b.bottom) - max(a.top, b.top)
    return overlap >= .5 * min(a.bottom - a.top, b.bottom - b.top)


def spatial_lines(page: DocumentPage) -> tuple[SpatialLine, ...]:
    spans = _view_spans(page)
    long_spans = [s for s in spans if s.box and len(s.text.strip()) >= 6]
    if long_spans and sum(s.box.bottom - s.box.top > s.box.right - s.box.left for s in long_spans) > len(long_spans) / 2:
        # Sideways text without usable header anchors cannot safely be joined
        # by its raw y-coordinate. Preserve the OCR reading order instead.
        return tuple(SpatialLine((span,)) for span in spans)
    positioned = sorted((s for s in spans if s.box),
                        key=lambda s: ((s.box.top + s.box.bottom) / 2, s.box.left))
    groups: list[list[TextSpan]] = []
    for span in positioned:
        if groups and all(_same_line(existing, span) for existing in groups[-1]):
            groups[-1].append(span)
        else:
            groups.append([span])
    # No geometry means no inferred relationship to another span.
    return tuple(SpatialLine(tuple(sorted(group, key=lambda s: s.box.left))) for group in groups) + tuple(
        SpatialLine((span,)) for span in spans if span.box is None
    )
