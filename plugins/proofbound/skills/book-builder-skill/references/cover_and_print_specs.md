# Cover and Print Specifications

This document provides print-ready cover specifications for Amazon KDP and Lulu print-on-demand services. Use these specifications when designing book covers for professional printing.

## Supported Trim Sizes

ProofBound generates books at three trim sizes; pick one per book and the system flows the geometry into the Quarto interior PDF and the matching cover template(s).

| Key    | Dimensions                | Notes                                                                                                       |
| ------ | ------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 6x9    | 6.0" × 9.0" (152×229mm)   | US Trade default. KDP + Lulu supported.                                                                     |
| a5     | 5.83" × 8.27" (148×210mm) | Lulu only. Proofbound does not currently generate a KDP A5 cover, so the KDP cover is skipped for A5 books. |
| digest | 5.5" × 8.5" (140×216mm)   | Compact standard KDP trim. KDP + Lulu supported (both covers emit).                                         |

Examples below use 6×9 dimensions; substitute A5 (5.83×8.27in) or Digest (5.5×8.5in) dimensions for that trim. The bleed and safe-zone offsets are the same for all trims. The spine-width formula differs by publisher: Lulu adds a 0.06" cover-stock offset; KDP is the bare `pages × 0.002252` (no offset).

## Quick Reference (6×9 example)

| Specification               | Amazon KDP                  | Lulu                        |
| --------------------------- | --------------------------- | --------------------------- |
| **Trim Size**               | 6" × 9" (152.4mm × 228.6mm) | 6" × 9" (152.4mm × 228.6mm) |
| **Bleed**                   | 0.125" (3.175mm) all edges  | 0.125" (3.175mm) all edges  |
| **Spine Width (200 pages)** | 0.450"                      | 0.510"                      |
| **Total Width (200 pages)** | 12.700"                     | 12.770"                     |
| **Total Height**            | 9.25"                       | 9.25"                       |
| **Color Mode**              | RGB or CMYK                 | RGB or CMYK                 |
| **Resolution**              | 300 DPI minimum             | 300 DPI minimum             |
| **Format**                  | PDF/X-1a:2001 or PDF 1.3+   | PDF 1.3+                    |

---

## Amazon KDP (Kindle Direct Publishing)

### Trim Size

**Standard Book**: 6" × 9" (152.4mm × 228.6mm)

This is the most common trade paperback size, suitable for:

- Non-fiction books
- Self-help and how-to guides
- Business books
- Cookbooks
- Memoirs

### Bleed

**Required Bleed**: 0.125" (3.175mm) on all edges

Bleed ensures that background colors and images extend beyond the trim edge. After trimming, the bleed area is cut off, preventing white borders.

**Important**: All critical content (text, logos) must stay at least 0.25" (6.35mm) inside the trim line to avoid being cut off.

### Spine Width Calculation

**Formula**: `page_count × 0.002252` inches

KDP adds **no** cover-stock offset — that 0.06" allowance is Lulu's alone. All figures round to 3
decimal places, matching `CoverTemplate.get_spine_width_in()`, which is the single source of truth for
both publishers.

**Examples**:

- 100 pages: `100 × 0.002252 = 0.225"` (narrow spine)
- 200 pages: `200 × 0.002252 = 0.450"`
- 300 pages: `300 × 0.002252 = 0.676"`
- 400 pages: `400 × 0.002252 = 0.901"`

**Minimum Pages for Spine Text**: ~100 pages (0.225" spine) - text becomes difficult to read on narrower spines

### Total Cover Dimensions

**Formula**:

```
Width = (2 × trim_width) + spine_width + (2 × bleed)
Height = trim_height + (2 × bleed)
```

**Example (200-page book)**:

```
Width = (2 × 6") + 0.450" + (2 × 0.125")
      = 12" + 0.450" + 0.25"
      = 12.700"

Height = 9" + (2 × 0.125")
       = 9.25"
```

### Color and Quality

- **Color Mode**: RGB recommended (KDP converts to CMYK automatically)
- **Resolution**: 300 DPI minimum (600 DPI for line art)
- **PDF Format**: PDF/X-1a:2001 or PDF 1.3+
- **Fonts**: Embed all fonts in PDF

### Safe Zones

| Zone                 | Distance from Trim       | Purpose                  |
| -------------------- | ------------------------ | ------------------------ |
| **Critical Content** | 0.25" (6.35mm) inside    | Text, logos, barcodes    |
| **Safe Zone**        | 0.375" (9.525mm) inside  | Important graphics       |
| **Bleed Zone**       | 0.125" (3.175mm) outside | Background colors/images |

### Interior Margins (Gutter)

KDP also validates the **interior** PDF: the inside (gutter) margin must meet the tier for the page count, or the upload is rejected ("Insufficient gutter"). Outside, top and bottom need only 0.25" (0.375" with bleed).

| Pages   | Minimum inside margin |
| ------- | --------------------- |
| 24–150  | 0.375" (9.5mm)        |
| 151–300 | 0.5" (12.7mm)         |
| 301–500 | 0.625" (15.9mm)       |
| 501–700 | 0.75" (19.1mm)        |
| 701–828 | 0.875" (22.2mm)       |

ProofBound sets this for you: every render writes `inner=` into the Quarto PDF geometry (0.75" by default, which passes up to 700 pages, widened to 0.875" when the estimated or actual length calls for it) and records the value as `interior_gutter_in` on the book. The KDP publishing kit's pre-flight compares it with the tier for the final page count. Lulu publishes a similar table as a recommendation only and does not reject on margins.

---

## Lulu (Print-on-Demand)

### Trim Size

**Standard Book**: 6" × 9" (152.4mm × 228.6mm)

Same as KDP for consistency across platforms.

### Bleed

**Required Bleed**: 0.125" (3.175mm) on all edges

Identical to KDP specifications.

### Spine Width Calculation

**Formula**: `(page_count × 0.002252) + 0.06` inches

Same paper caliper as KDP, plus a 0.06" cover-stock allowance. We ship one stock (60# uncoated), so
there is no paper-type variant.

**Examples**:

- 100 pages: `(100 × 0.002252) + 0.06 = 0.285"` (narrow spine)
- 200 pages: `(200 × 0.002252) + 0.06 = 0.510"`
- 300 pages: `(300 × 0.002252) + 0.06 = 0.736"`
- 400 pages: `(400 × 0.002252) + 0.06 = 0.961"`

**Note**: the Lulu spine is always exactly 0.06" wider than the KDP spine for the same page count, so
the two covers are genuinely different sizes — a KDP cover PDF is not a valid Lulu cover PDF, or the
reverse.

### Total Cover Dimensions

**Formula**:

```
cover_width = trim_width + bleed + panel_buffer = 6" + 0.125" + 0.005" = 6.130"
cover_height = trim_height + (2 × bleed) = 9" + 0.25" = 9.25"

Width = (2 × cover_width) + spine_width
Height = cover_height
```

**Example (200-page book)**:

```
Width = (2 × 6.130") + 0.510"
      = 12.26" + 0.510"
      = 12.770"

Height = 9.25"
```

**Don't drop the 0.005" per-panel buffer.** Lulu's panel is 6.130", not 6.125" (`cover_width_in` on
`lulu_6x9`); using 6.125" yields 12.760" — a wrap 0.010" too narrow. KDP panels have no buffer.

**Key Difference from KDP**: two differences, and they compound. Lulu's formula includes bleed in the
`cover_width` term while KDP adds it separately — that part is bookkeeping, and both spellings agree.
But Lulu's spine also carries the +0.06" cover-stock allowance, so the wraps differ by 0.070" at 200pp
(**KDP 12.700" vs Lulu 12.770"**). Compose one cover per publisher; never reuse one for the other.

### Color and Quality

- **Color Mode**: RGB or CMYK
- **Resolution**: 300 DPI minimum
- **PDF Format**: PDF 1.3+
- **Fonts**: Embed all fonts

### API Validation

Lulu provides a Cover Dimensions API endpoint for programmatic validation:

**Endpoint**: `POST https://api.lulu.com/cover-dimensions/`

**Request**:

```json
{
  "page_count": 200,
  "trim_size": "US_TRADE",
  "paper_type": "STANDARD"
}
```

**Response** (shape only — treat the figures as illustrative):

```json
{
  "width_mm": 324.36,
  "height_mm": 235.585,
  "spine_width_mm": 12.95
}
```

For the number we actually compose against, use `CoverTemplate.get_spine_width_in()` rather than this
endpoint: 200pp 6×9 Lulu = 0.510" = 12.95mm.

---

## Cover Geometry

### Component Layout

A complete print-ready cover consists of three sections:

```
┌─────────────────────────────────────────────────────────┐
│  ◄─bleed─►                              ◄─bleed─►       │
│ ┌─────────────┬──────┬─────────────────────────┐       │
│ │             │      │                         │       │  ▲
│ │             │      │                         │       │  │
│ │    BACK     │ SPINE│        FRONT            │       │  │ bleed
│ │    COVER    │      │        COVER            │       │  │
│ │             │      │                         │       │  │
│ │             │      │                         │       │  ▼
│ └─────────────┴──────┴─────────────────────────┘       │
│                                              ◄─bleed─►  │
└─────────────────────────────────────────────────────────┘
     ◄─trim─►   ◄spine─► ◄────────trim────────►
     6"         variable  6"
```

### Front Cover

- **Position**: Right third of cover PDF
- **Dimensions**: 6" × 9" (trim) + 0.125" bleed on right, top, bottom
- **Content**: Title, subtitle, author, cover artwork
- **Safe zone**: Keep text 0.25" from trim edges

### Spine

- **Position**: Center strip between front and back
- **Width**: Variable based on page count (see formulas above)
- **Content**: Book title, author name (if spine ≥ 0.3")
- **Orientation**: Text reads vertically from bottom to top (standard)
- **Safe zone**: Keep text 0.0625" from spine edges

**Spine Text Guidelines**:

- **0.25" - 0.35"**: Only title (small font)
- **0.35" - 0.5"**: Title + Author (small font)
- **0.5"+**: Title + Author + Logo (comfortable spacing)

### Back Cover

- **Position**: Left third of cover PDF
- **Dimensions**: 6" × 9" (trim) + 0.125" bleed on left, top, bottom
- **Content**: Book description, author bio, ISBN barcode
- **Barcode Position**: Bottom right corner, 0.5" from trim edges
- **Safe zone**: Keep text 0.25" from trim edges

---

## Bleed Zones

### What is Bleed?

Bleed is the area beyond the trim line where background colors and images extend. This compensates for minor cutting variations during trimming.

### Bleed Requirements

- **Size**: 0.125" (3.175mm) on all edges
- **Content**: Extend background colors/images into bleed area
- **Text**: NEVER place critical text in bleed area

### Visual Guide

```
┌──────────────────────────────┐
│ ◄─── 0.125" Bleed Zone ─────►│ (will be cut off)
│ ┌──────────────────────────┐ │
│ │                          │ │
│ │   ◄─ 0.25" Safe Zone ──► │ │ (critical text area)
│ │   ┌──────────────────┐   │ │
│ │   │                  │   │ │
│ │   │  Trim Line (6"×9")  │ │
│ │   │                  │   │ │
│ │   └──────────────────┘   │ │
│ │                          │ │
│ └──────────────────────────┘ │
└──────────────────────────────┘
```

---

## Common Issues and Solutions

### Issue: White Lines Along Trim Edge

**Cause**: Background doesn't extend into bleed area
**Solution**: Extend all background colors/images 0.125" beyond trim line

### Issue: Text Cut Off

**Cause**: Text too close to trim line
**Solution**: Keep all text at least 0.25" inside trim line

### Issue: Spine Text Misaligned

**Cause**: Incorrect spine width calculation
**Solution**: Use exact page count from final PDF, apply correct formula

### Issue: Low Resolution Images

**Cause**: Images below 300 DPI
**Solution**: Use high-resolution images (300 DPI minimum)

### Issue: Fonts Not Embedded

**Cause**: Fonts not included in PDF
**Solution**: Embed all fonts when generating PDF

---

## Trim Sizes Reference

ProofBound currently produces books at the three trims listed in "Supported Trim Sizes" above (6×9, A5, and Digest). The table below catalogs other common industry trims for context; they are not yet wired into the pipeline.

| Trim Size            | Use Case                                       |
| -------------------- | ---------------------------------------------- |
| 5" × 8"              | Fiction, novels                                |
| 5.5" × 8.5" (Digest) | **Fiction, memoirs (supported)**               |
| 5.83" × 8.27" (A5)   | **International trade (supported)**            |
| 6" × 9"              | **Non-fiction, US trade (supported, default)** |
| 7" × 10"             | Textbooks, technical books                     |
| 8" × 10"             | Photo books, coffee table books                |
| 8.5" × 11"           | Workbooks, manuals                             |

---

## Proofbound Implementation

Proofbound generates print-ready covers using:

1. **Artwork Generation**: AI-generated cover images via Ideogram/DALL-E
2. **Text Overlay**: Title, subtitle, author overlaid on artwork
3. **Spine Calculation**: Automatic based on PDF page count
4. **KDP PDF**: Optimized for Amazon KDP (includes barcode placeholder)
5. **Lulu PDF**: Optimized for Lulu (follows API specs)

**Files Generated**:

- `cover_kdp.pdf` - Amazon KDP print-ready cover
- `cover_lulu.pdf` - Lulu print-ready cover
- `cover_front_artwork.png` - Original AI-generated artwork
- `cover_kdp_thumb.jpg` - KDP cover thumbnail
- `cover_lulu_thumb.jpg` - Lulu cover thumbnail

---

## Testing & Validation

### Pre-Print Checklist

- [ ] PDF dimensions match calculated totals (width/height)
- [ ] Bleed extends 0.125" on all edges
- [ ] Spine width matches page count formula
- [ ] All text is at least 0.25" inside trim lines
- [ ] Images are 300 DPI or higher
- [ ] Fonts are embedded
- [ ] Color mode is RGB or CMYK
- [ ] PDF format is PDF/X-1a:2001 or PDF 1.3+

### Validation Tools

- **KDP Cover Calculator**: [https://kdp.amazon.com/en_US/cover-calculator](https://kdp.amazon.com/en_US/cover-calculator)
- **Lulu Cover API**: [https://api.lulu.com/docs/#tag/Files-validation](https://api.lulu.com/docs/#tag/Files-validation)
- **PDF Inspector**: Check embedded fonts, color mode, resolution

---

## Official Documentation

- **Amazon KDP**: [Cover Guidelines](https://kdp.amazon.com/en_US/help/topic/G201953020)
- **Lulu**: [Cover Creation Guide](https://www.lulu.com/en/us/cover-creation)
- **Lulu API**: [Cover Dimensions Endpoint](https://api.lulu.com/docs/#tag/Files-validation/operation/Cover-Dimensions_create)
