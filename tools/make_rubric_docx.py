#!/usr/bin/env python3
"""Generate school-format (RUBIC1.docx style) English rubric sheets as .docx.

Usage:  python3 tools/make_rubric_docx.py
Output: wl_benchmark/tasks_data/essay/_source/RUBIC-<genre>.docx

Pure stdlib (zipfile + hand-built WordprocessingML), mirroring the layout of
the school-provided RUBIC1.docx: grade-band table, three scored dimensions
(IC 25 / ORG 25 / LANG 50) with five levels, deductions block, grade mapping,
FINAL SCORE / COMMENTS lines, plus the JSON contract for the GLM judge.
"""
from __future__ import annotations

import os
import zipfile
from xml.sax.saxutils import escape

OUT_DIR = os.path.join(os.path.dirname(__file__), "..",
                       "tasks_data", "essay", "_source")

LEVELS = ["Exemplary", "Convincing", "Acceptable",
          "Needs improvement", "Not satisfactory"]

IC_ORG_BANDS = ("A-range 25-20 | B-range 19-17 | C-range 16-14 | "
                "D-range 13 | F-range 12-0")
LANG_BANDS = ("A-range 50-40 | B-range 39-34 | C-range 33-28 | "
              "D-range 27-25 | F-range 24-0")
GRADE_MAP = ("A = 86    A- = 80    B+ = 76    B = 72    B- = 68    "
             "C+ = 64    C = 60    C- = 56    D+ = 53    D = 50    F = <50")

LANG_CHECKLIST = (
    "Basic: Fragment. Run-on. Sentence length. SVO. Word order/location. "
    "Missing word. Wordy. Verb tense. SV agreement. Verb phrases. Active verbs. "
    "Singular/Plural. Spelling. Articles. Prepositions. Pronouns. Conjunctions. "
    "Adjectives. Adverbs. Capitalization. Punctuation. Spacing. Meaning unclear. "
    "Awkward phrase. Repetitious. Fronted adverbial conjunctions. Absolutes. "
    "Cliches. Slang. Hyperbole. Profanity. "
    "Advanced: Qualifying language. Possessive vs. prepositional phrases. "
    "Style. Voice. "
    "Vocabulary: Limited vocabulary. Incorrect word form. Incorrect word use. "
    "Poor word choice.")

JSON_NOTE = (
    'Scoring output (automated GLM judge): strict JSON only — {"score": <0-100 '
    'after deductions>, "max": 100, "breakdown": [Intellectual Content 25 / '
    'Organization 25 / Language Use 50], "deductions": [...], "summary": "..."}')

# --------------------------------------------------------------- XML helpers
def r(text, bold=False, sz=22):
    pr = ""
    if bold or sz != 22:
        pr = ("<w:rPr>" + ("<w:b/>" if bold else "") +
              f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr>')
    return f'<w:r>{pr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def p(text, bold=False, sz=22, after=120):
    return (f'<w:p><w:pPr><w:spacing w:after="{after}"/></w:pPr>'
            f'{r(text, bold, sz)}</w:p>')


def cell(text, bold=False, w=2600, sz=20):
    return (f'<w:tc><w:tcPr><w:tcW w:w="{w}"/></w:tcPr>'
            f'<w:p><w:pPr><w:spacing w:after="40"/></w:pPr>'
            f'{r(text, bold, sz)}</w:p></w:tc>')


def table(rows, widths=None):
    n = len(rows[0])
    widths = widths or [9360 // n] * n
    border = ('<w:tblBorders>' +
              "".join(f'<w:{s} w:val="single" w:sz="4" w:color="999999"/>'
                      for s in ("top", "left", "bottom", "right",
                                "insideH", "insideV")) +
              '</w:tblBorders>')
    xml = ('<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
           f'{border}</w:tblPr><w:tblGrid>' +
           "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) +
           "</w:tblGrid>")
    for row in rows:
        xml += ("<w:tr>" +
                "".join(cell(t, bold=(i == 0 and row is rows[0]),
                             w=widths[i])
                        for i, t in enumerate(row)) +
                "</w:tr>")
    return xml + "</w:tbl>"


def dimension_table(name, points, criterion, descriptors):
    """One dimension: heading, criterion paragraph, 5-level table."""
    xml = p(f"{name}  ({points} points)", bold=True, sz=24)
    xml += p(criterion)
    xml += table([["Level", "Descriptors"]] +
                 [[lv, descriptors[lv]] for lv in LEVELS],
                 widths=[2000, 7360])
    return xml + p("", after=60)


def sheet(title, subtitle, ic, org, lang_levels, deductions):
    body = p(title, bold=True, sz=30, after=60)
    body += p(subtitle, sz=20, after=160)
    body += p("Score bands (per dimension)", bold=True)
    body += table([["Level", "25-pt dims (IC / ORG)", "50-pt dim (Language)"],
                   ["A-range / Exemplary", "25-20", "50-40"],
                   ["B-range / Convincing", "19-17", "39-34"],
                   ["C-range / Acceptable", "16-14", "33-28"],
                   ["D-range / Needs improvement", "13", "27-25"],
                   ["F-range / Not satisfactory", "12-0", "24-0"]],
                  widths=[3360, 3000, 3000])
    body += p("", after=60)
    body += dimension_table("INTELLECTUAL CONTENT / THINKING", 25,
                            "Good / Average / Weak Thinking (choose one). "
                            + ic["criterion"], ic["levels"])
    body += dimension_table("ORGANIZATION", 25, org["criterion"], org["levels"])
    body += dimension_table("LANGUAGE USE", 50, LANG_CHECKLIST, lang_levels)
    body += p("Total (100 points max):", bold=True)
    body += p(deductions)
    body += p("Points deducted: ______")
    body += p("Grade mapping:  " + GRADE_MAP)
    body += p("FINAL SCORE: ____________          COMMENTS: ______________________",
              bold=True, after=200)
    body += p(JSON_NOTE, sz=20)
    return body


def write_docx(path, body_xml):
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType='
        '"application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/></Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + body_xml +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
        '</w:sectPr></w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)


# ------------------------------------------------------------------ content
SHEETS = {}

SHEETS["storytelling"] = dict(
    title="RUBRIC - STORY TELLING",
    subtitle="Task: a complete English short story about Qin Shi Huang riding "
             "a Polar Bear and looking at his phone (markdown output). All "
             "three prompt elements must appear and matter to the plot. "
             "Adapted from the official CUHKSZ composition rubric (RUBIC1.docx).",
    ic=dict(
        criterion="Understands the material. Main points support the topic. "
                  "No main points missing. No factual errors. Interesting, "
                  "relevant theme. Keeps focus. Main points clear. The "
                  "relationship between the details and the main idea is made "
                  "clear. Good examples and specific supporting details. Vivid "
                  "description makes the experience real.",
        levels={
            "Exemplary": "The absurd premise (Qin Shi Huang / polar bear / "
                         "phone) is woven into the plot causally, not pasted as "
                         "decoration. Historical details about Qin Shi Huang are "
                         "accurate; details-theme links explicit; vivid, "
                         "concrete scenes.",
            "Convincing": "All three elements play a real role in the story; "
                          "minor factual looseness; details mostly support the "
                          "theme.",
            "Acceptable": "Elements present but at least one is cosmetic; some "
                          "details float free of the main idea; mild factual "
                          "oddities.",
            "Needs improvement": "Elements listed but not integrated; story "
                                 "unclear or generic; notable factual errors.",
            "Not satisfactory": "One or more required elements missing; no "
                                "discernible story.",
        }),
    org=dict(
        criterion="Paper level: Strong introduction. Strong conclusion. Logical "
                  "and convincing progression of ideas. Consistent and "
                  "appropriate point-of-view. Paragraph level: Effective "
                  "transitions connecting paragraphs if needed. Paragraph "
                  "boundaries recognized. Internal paragraph coherence "
                  "maintained. Other: Indents.",
        levels={
            "Exemplary": "Clear beginning-conflict-climax-resolution arc; a "
                         "strong hook and a resonant ending; consistent POV and "
                         "tense; scene shifts bridged by effective transitions.",
            "Convincing": "Full arc present; pacing occasionally uneven; POV/"
                          "tense consistent with at most one slip.",
            "Acceptable": "Arc recognizable but one stage weak (e.g. rushed "
                          "climax); mechanical or missing transitions between "
                          "scenes.",
            "Needs improvement": "Missing conflict or resolution; abrupt jumps; "
                                 "frequent POV/tense drift.",
            "Not satisfactory": "No narrative arc; disconnected fragments.",
        }),
    lang_levels={
        "Exemplary": "Near error-free across the Basic checklist; advanced "
                     "control (varied sentence openings, qualifying language, "
                     "consistent narrative voice); precise, varied vocabulary "
                     "with intentional word choice.",
        "Convincing": "Few basic errors, none blocking comprehension; some "
                      "stylistic variety; vocabulary adequate with occasional "
                      "imprecision.",
        "Acceptable": "Recurring basic errors (tense/agreement/articles) but "
                      "meaning intact; repetitive vocabulary; little stylistic "
                      "range.",
        "Needs improvement": "Dense basic errors; frequent awkward/repetitious "
                             "phrasing; meaning strained at points.",
        "Not satisfactory": "Errors obscure meaning; extremely limited "
                            "vocabulary.",
    },
    deductions="Instructions followed: story deviates from the given premise, "
               "up to -10; any required element absent: -10. Length: out of "
               "range -3 per 100 words over/under. Language: not an English "
               "story, total capped at 50. Off-premise content or non-narrative "
               "prose: total capped at 54. Other (explain in summary).")

SHEETS["argument"] = dict(
    title="RUBRIC - ARGUMENT WRITING",
    subtitle="Task: an English argument essay on why we could use smartphones "
             "while walking inside the campus (markdown output). At least "
             "three evidences and a rebuttal paragraph criticizing the claim "
             "'bu-lu-bu-kan-shou-ji' (do not look at your phone while walking). "
             "Adapted from the official CUHKSZ composition rubric (RUBIC1.docx).",
    ic=dict(
        criterion="Understands the material. Main points support the topic. "
                  "No main points missing. No factual errors. Interesting, "
                  "relevant theme. Keeps focus. Main points clear. The "
                  "relationship between the details and the main idea is made "
                  "clear. Good examples and specific supporting details.",
        levels={
            "Exemplary": "Three or more distinct, concrete evidences (campus "
                         "scenarios, plausible data, authorities, analogies), "
                         "each explicitly tied back to the thesis; rebuttal "
                         "genuinely engages the safety concern behind the "
                         "counter-claim and rebuts it with reasoning and "
                         "evidence; no factual errors; CUHKSZ-specific "
                         "grounding where sensible.",
            "Convincing": "Three evidences present and relevant; rebuttal "
                          "addresses the counter-claim, though somewhat brief; "
                          "minor factual looseness.",
            "Acceptable": "Only two solid evidences, or evidence generic and "
                          "repetitive; rebuttal present but shallow (assertion "
                          "without reasoning); occasional focus drift.",
            "Needs improvement": "One or zero real evidences; rebuttal missing "
                                 "or merely restates the thesis; noticeable "
                                 "factual errors.",
            "Not satisfactory": "No identifiable argument; evidences absent; "
                                "off-topic.",
        }),
    org=dict(
        criterion="Paper level: Strong introduction. Strong conclusion. Logical "
                  "and convincing progression of ideas. Consistent and "
                  "appropriate point-of-view. Paragraph level: Effective "
                  "transitions connecting paragraphs if needed. Paragraph "
                  "boundaries recognized. Internal paragraph coherence "
                  "maintained. Other: Indents.",
        levels={
            "Exemplary": "Thesis stated precisely in the introduction; each "
                         "evidence gets its own coherent paragraph with a topic "
                         "sentence; rebuttal placed strategically before the "
                         "conclusion with a smooth pivot; conclusion synthesizes "
                         "rather than repeats.",
            "Convincing": "Clear intro-body-conclusion; paragraph order "
                          "logical; transitions mostly effective; rebuttal "
                          "positioned reasonably.",
            "Acceptable": "Structure recognizable but paragraphing weak "
                          "(multiple ideas crammed, or one-sentence "
                          "paragraphs); transitions mechanical without logical "
                          "glue.",
            "Needs improvement": "No clear thesis or paragraphs lack boundaries; "
                                 "rebuttal bolted on randomly; ideas ordered by "
                                 "accident.",
            "Not satisfactory": "No essay structure; disconnected sentences.",
        }),
    lang_levels={
        "Exemplary": "Near error-free across the Basic checklist; advanced "
                     "control: hedged claims where evidence is probabilistic "
                     "('may', 'tends to'), argumentative voice without slang/"
                     "hyperbole/profanity; precise connectives (concession, "
                     "cause, contrast) beyond and/but/so.",
        "Convincing": "Few basic errors; claims mostly well qualified; "
                      "vocabulary adequate, occasional poor word choice.",
        "Acceptable": "Recurring basic errors but meaning intact; absolutes and "
                      "over-claiming ('everyone knows', 'always'); repetitious "
                      "wording.",
        "Needs improvement": "Dense basic errors; wordy or awkward phrasing "
                             "throughout; over-claiming and informal register.",
        "Not satisfactory": "Errors obscure meaning; no argumentative register.",
    },
    deductions="Instructions followed: fewer than three evidences, -10; "
               "rebuttal paragraph absent, -10; essay argues the opposite "
               "claim, -25. Length: out of range -3 per 100 words. Language: "
               "not an English essay, total capped at 50. Structure: no essay "
               "form (bullet list only / chat reply), total capped at 54. "
               "Other (explain in summary).")

SHEETS["proposal"] = dict(
    title="RUBRIC - RESEARCH PROPOSAL WRITING",
    subtitle="Task: an English research proposal on whether we could use "
             "smartphones while walking inside the campus (markdown output), "
             "with background / literature review / research method / expected "
             "result / conclusion. Must incorporate the results of the previous "
             "two writings (story and argument essay, given as context). "
             "Adapted from the official CUHKSZ composition rubric (RUBIC1.docx).",
    ic=dict(
        criterion="Understands the material. Main points support the topic. "
                  "No main points missing. No factual errors. Interesting, "
                  "relevant theme. Keeps focus. Main points clear. The "
                  "relationship between the details and the main idea is made "
                  "clear. Good examples and specific supporting details.",
        levels={
            "Exemplary": "Research question is precise and testable "
                         "(population, setting, variables). Method names its "
                         "design (field observation / survey / experiment), "
                         "measures, and an operational definition of 'phone use "
                         "while walking'; ethical note present. Both previous "
                         "writings are explicitly integrated - the argument "
                         "essay's claims become hypotheses, the story supplies "
                         "scenarios or motivation - with the connection made "
                         "explicit. No factual errors.",
            "Convincing": "Clear question and reasonable method; both prior "
                          "writings referenced but integration is mechanical "
                          "(quoted without being turned into research "
                          "elements); minor factual looseness.",
            "Acceptable": "Question vague or method generic (a survey of whom, "
                          "measuring what?); only one prior writing used, or "
                          "mentioned in passing; some factual oddities.",
            "Needs improvement": "Method not actionable; prior writings absent "
                                 "or mentioned in one sentence; focus drifts "
                                 "from the research topic.",
            "Not satisfactory": "No research design at all; prior context "
                                "ignored; off-topic.",
        }),
    org=dict(
        criterion="Paper level: Strong introduction. Strong conclusion. Logical "
                  "and convincing progression of ideas. Consistent and "
                  "appropriate point-of-view. Paragraph level: Effective "
                  "transitions connecting paragraphs if needed. Paragraph "
                  "boundaries recognized. Internal paragraph coherence "
                  "maintained. Other: Indents.",
        levels={
            "Exemplary": "All five required sections present under clear "
                         "headings, each doing its own job: background "
                         "motivates, literature review positions the gap, "
                         "method is step-by-step reproducible, expected result "
                         "follows logically from method, conclusion ties back. "
                         "Markdown headings clean and consistent.",
            "Convincing": "All five sections present; content mostly in the "
                          "right section; headings consistent; transitions "
                          "adequate.",
            "Acceptable": "One section missing or mislocated (e.g. results-like "
                          "content inside method); heading structure "
                          "inconsistent; some sections underdeveloped.",
            "Needs improvement": "Two or more sections missing or empty "
                                 "shells; no heading discipline; sections blur "
                                 "into each other.",
            "Not satisfactory": "No proposal structure; disconnected text.",
        }),
    lang_levels={
        "Exemplary": "Near error-free across the Basic checklist; academic "
                     "register throughout: hedged claims, formal style and "
                     "objective voice, no cliches/slang/hyperbole; "
                     "discipline-appropriate terminology (participants, "
                     "instrument, sample, operationalise); consistent verb "
                     "tense per section.",
        "Convincing": "Few basic errors; mostly academic register with "
                      "occasional informality; terminology mostly apt.",
        "Acceptable": "Recurring basic errors but meaning intact; register "
                      "wavers between academic and conversational; wordy "
                      "passages.",
        "Needs improvement": "Dense basic errors; conversational tone dominates; "
                             "poor word choice hampers precision.",
        "Not satisfactory": "Errors obscure meaning; no academic register.",
    },
    deductions="Instructions followed: any required section missing, -5 each; "
               "prior writings not incorporated, -10. Length: out of range -3 "
               "per 150 words. Language: not in English, total capped at 50. "
               "Fabrication: invented literature/citations presented as real "
               "without illustrative marking, total capped at 40. Not a "
               "proposal (plain essay without method/expected result), total "
               "capped at 54. Other (explain in summary).")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for genre, s in SHEETS.items():
        path = os.path.normpath(os.path.join(OUT_DIR, f"RUBIC-{genre}.docx"))
        write_docx(path, sheet(s["title"], s["subtitle"],
                               s["ic"], s["org"], s["lang_levels"],
                               s["deductions"]))
        print("wrote", path)


if __name__ == "__main__":
    main()
