import csv
import json
import logging
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.src import JBGFindingsRegister as findings_register
from app.src import JBGHistory as history
from app.src import JBGManagementReport as management_report
from app.src import JBGMetricSchema as schema
from app.src import JBGValidation as validation
from app.src import JBGYearComparison as comparison
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer
from app.src.JBGFundNames import FundNameResolver

logger = logging.getLogger(__name__)

class JsonConverter:
    def __init__(self, json_path: str | Path, include_sources: bool = False):
        self.json_path = Path(json_path)
        if not self.json_path.exists():
            raise FileNotFoundError(f"JSON file not found: {self.json_path}")
        self.include_sources = include_sources
        self.data = self._load_json()

    METADATA_PREFIX = "_"

    def _load_json(self):
        with open(self.json_path, encoding='utf-8') as f:
            return json.load(f)

    def _funds(self) -> dict:
        """The fund entries only. Keys prefixed with an underscore hold
        metadata such as the validation findings, not a fund."""
        return {
            name: years
            for name, years in self.data.items()
            if not name.startswith(self.METADATA_PREFIX) and isinstance(years, dict)
        }

    SKIPPED_KEY = "_ejanalyserade"
    MANAGEMENT_REPORT_KEY = "_forvaltningsberattelse"
    MANAGEMENT_SHEET = "Förvaltningsberättelse"
    COMPARISON_SHEET = "Förändring mot föregående år"
    SUMMARY_KEY = "_korningen"
    REGISTER_SHEET = "Återkommande anmärkningar"
    MIN_OCCURRENCES_LISTED = 2

    def management_summaries(self) -> dict:
        """Sammanfattningarna ur förvaltningsberättelsen, per kassa."""
        recorded = self.data.get(self.MANAGEMENT_REPORT_KEY) or {}
        return recorded if isinstance(recorded, dict) else {}

    def skipped(self) -> list[dict]:
        """Dokument som inte kunde analyseras, med orsak.

        Ligger i resultatfilen men filtrerades bort av `_funds`, och syntes
        därför bara i loggen. En kolumn som saknas är svårare att upptäcka än
        en cell som är fel: den som summerar raden får ett rimligt tal, bara
        räknat på färre kassor än hon tror.
        """
        recorded = self.data.get(self.SKIPPED_KEY) or []
        return recorded if isinstance(recorded, list) else []

    def findings(self) -> list[dict]:
        """Validation findings recorded in the result file, if any."""
        recorded = self.data.get("_rimlighetskontroller") or []
        return recorded if isinstance(recorded, list) else []

    def _findings_by_cell(self) -> dict:
        index = {}
        for finding in self.findings():
            for metric in finding.get("berörda_nyckeltal") or []:
                key = (finding.get("kassa"), str(finding.get("år")), metric)
                index.setdefault(key, []).append(finding.get("kontroll", ""))
        return index

    def _rows(self) -> list[dict]:
        """Flatten to Fund | Year | Key | Value [| Source | Certainty | Comment].

        The certainty and comment the model produces used to be discarded here,
        even though the prompt spends considerable effort calibrating them.
        """
        rows = []
        flagged = self._findings_by_cell()
        for fund_name, years in self._funds().items():
            for year, key_numbers in years.items():
                for key, value_dict in key_numbers.items():
                    if not isinstance(value_dict, dict):
                        continue
                    row = {
                        "Fund": fund_name,
                        "Year": year,
                        "Key": key,
                        "Value": value_dict.get(JBGAnnualReportAnalyzer.FIELD_VALUE),
                    }
                    if self.include_sources:
                        row["Source"] = value_dict.get(JBGAnnualReportAnalyzer.FIELD_SOURCE)
                        row["Certainty"] = value_dict.get(
                            JBGAnnualReportAnalyzer.FIELD_CERTAINTY
                        )
                        row["Comment"] = value_dict.get(
                            JBGAnnualReportAnalyzer.FIELD_COMMENT
                        )
                        row["Validering"] = "; ".join(
                            flagged.get((fund_name, str(year), key), [])
                        )
                    rows.append(row)
        return rows

    @property
    def columns(self) -> list[str]:
        base = ["Fund", "Year", "Key", "Value"]
        if not self.include_sources:
            return base
        return base + ["Source", "Certainty", "Comment", "Validering"]

    def to_dataframe(self):
        """Optional convenience wrapper. Requires the 'analysis' extra.

        pandas is imported here rather than at module level: it was a hard
        dependency of the whole package purely so that to_csv could write a
        semicolon-separated file, which the standard library does fine.
        """
        try:
            import pandas as pd
        except ImportError as ex:  # pragma: no cover
            raise ImportError(
                "to_dataframe() kräver pandas. Installera med: pip install '.[analysis]'"
            ) from ex
        return pd.DataFrame(self._rows(), columns=self.columns)

    def to_csv(self, output_path: str | Path):
        output_path = Path(output_path)
        rows = self._rows()
        # utf-8-sig so Excel on Windows opens it with the right encoding, and
        # semicolons because that is what a Swedish locale expects.
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=self.columns, delimiter=";", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"CSV file saved to {output_path} ({len(rows)} rader)")

    def to_excel(self, output_path: str | Path, by: str = "fund"):
        """
        Save to Excel with multiple sheets.
        by: 'fund' or 'year'
        """
        import pandas as pd  # only needed for this optional flat export

        df = self.to_dataframe()
        output_path = Path(output_path)

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            if by == "fund":
                for fund, group in df.groupby("Fund"):
                    group.to_excel(writer, sheet_name=self._sanitize_sheetname(fund), index=False)
            elif by == "year":
                for year, group in df.groupby("Year"):
                    group.to_excel(writer, sheet_name=str(year), index=False)
            else:
                raise ValueError("Parameter 'by' must be either 'fund' or 'year'")

        logger.info(f"Excel with sheets by '{by}' saved to {output_path}")

    # Confidence bands used to shade value cells. Read straight off the
    # "säkerhet" the model reports, which the prompt calibrates explicitly.
    CERTAINTY_BANDS = [
        (schema.CERTAINTY_EXPLICIT, "C6EFCE", "explicit – står ordagrant i dokumentet"),
        (schema.CERTAINTY_DERIVED, "FFEB9C", "härledd – uträknad eller tolkad rubrik"),
        (schema.CERTAINTY_UNCERTAIN, "FFC7CE", "osäker – bör kontrolleras mot källan"),
    ]
    FLAGGED_FILL = "E1BEE7"
    # Upplysningar är inga fel. De får en egen, lugnare ton, annars läses de
    # som anmärkningar och dränker dem som verkligen är det.
    INFO_FILL = "DDEBF7"

    @classmethod
    def _certainty_fill(cls, certainty) -> PatternFill | None:
        """Shade by the reported level, mapping legacy floats onto the scale."""
        level = schema.certainty_level(certainty)
        for name, colour, _ in cls.CERTAINTY_BANDS:
            if level == name:
                return PatternFill(start_color=colour, end_color=colour, fill_type="solid")
        return None

    RATIO_GROUP = "📐 Nyckeltalsberäkningar"
    RATIO_FILENAME = "nyckeltalsberakningar.json"
    SOURCE_SHEET_SUFFIX = " med källa"

    @classmethod
    def _load_ratios(cls, ratio_def_path, key_def_path) -> list[dict]:
        """Ratio definitions, from an explicit path or next to the metrics.

        Missing is not an error: the file is an addition, and an export
        without it should still produce the figures.
        """
        path = Path(ratio_def_path) if ratio_def_path else (
            Path(key_def_path).parent / cls.RATIO_FILENAME
        )
        if not path.is_file():
            logger.info(f"Inga nyckeltalsberäkningar hittades på {path}.")
            return []
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as ex:
            logger.warning(f"Kunde inte läsa {path}: {ex}")
            return []

    def _write_ratio_rows(self, ws, row_idx, ratios, funds, fund_data, column_of) -> int:
        """Write the derived ratios as live Excel formulas.

        Formulas rather than computed values, because the point of these rows
        is that a reader can see what was divided by what, and correct an
        input without waiting for a new run. Each is wrapped in IFERROR so a
        fund missing a denominator gives an empty cell rather than #DIV/0!,
        and a fund missing any input at all is skipped entirely rather than
        being handed a zero that looks like a measurement.
        """
        if not ratios:
            return row_idx

        ws.cell(row=row_idx, column=1, value=self.RATIO_GROUP).font = Font(bold=True)
        row_idx += 1

        for ratio in ratios:
            label = ratio.get("Nyckeltal", "")
            formula = ratio.get("Formel", "")
            needed = re.findall(r"\{([^}]+)\}", formula)

            name_cell = ws.cell(row=row_idx, column=1, value=label)
            description = ratio.get("Beskrivning")
            if description:
                name_cell.comment = Comment(description, "Årsredovisningsgranskning")
                name_cell.comment.width = 380

            for fund in funds:
                column = column_of[fund]
                metrics = fund_data.get(fund, {})
                missing = [
                    name for name in needed
                    if (metrics.get(name) or {}).get(
                        JBGAnnualReportAnalyzer.FIELD_VALUE
                    ) is None
                ]
                cell = ws.cell(row=row_idx, column=column)
                if missing:
                    # Silence beats a number computed from gaps.
                    cell.comment = Comment(
                        "Beräknas inte: värde saknas för "
                        + ", ".join(sorted(missing)),
                        "Årsredovisningsgranskning",
                    )
                    cell.comment.width = 380
                    continue

                expression = formula
                for name in set(needed):
                    reference = f"{get_column_letter(column)}{self._metric_rows[name]}"
                    expression = expression.replace(f"{{{name}}}", reference)
                cell.value = f'=IFERROR({expression},"")'
                number_format = ratio.get("Format")
                if number_format:
                    cell.number_format = number_format
            row_idx += 1

        return row_idx

    def to_excel_by_year(
        self,
        output_path: str | Path,
        key_def_path: str | Path,
        fund_names: None | str | Path = None,
        findings: list | None = None,
        ratio_def_path: str | Path | None = None,
    ):
        """
        Export JSON data to Excel with:
        - Two sheets per year when sources are included: a clean one with the
          reported figures and the derived ratios, and a second carrying the
          source reference for every figure. The clean sheet is the one to
          work in; the other is for tracing a number back to its page.
        - Funds as columns
        - Nyckeltal as rows, grouped and ordered by key_def_path
        - Value cells shaded by the model's own reported certainty
        - The source, certainty and comment attached as a cell note
        - A separate sheet listing failed sanity checks, if any
        """
        with open(key_def_path, encoding="utf-8") as f:
            key_defs = json.load(f)

        # Resolve fund names through the shared resolver rather than an exact
        # dict lookup, which matched none of the names in a real sample.
        resolver = None
        if fund_names:
            try:
                resolver = FundNameResolver(fund_names)
            except (OSError, json.JSONDecodeError) as ex:
                logger.warning(f"Kunde inte läsa kassaregistret {fund_names}: {ex}")

        def display_name(fund: str) -> str:
            return resolver.short_name(fund) if resolver else fund

        grouped_keys = {}
        for entry in key_defs:
            group = entry.get("Grupp", "🧩 Övrigt")
            grouped_keys.setdefault(group, []).append(entry["Nyckeltal"])
        group_order = list(grouped_keys.keys())
        all_keys = [entry["Nyckeltal"] for entry in key_defs]

        if findings:
            flagged = validation.findings_by_cell(findings)
        else:
            # Fall back to whatever the result file recorded, so exporting an
            # existing JSON keeps the flags.
            flagged = {
                key: [
                    validation.Finding(
                        fund=key[0], year=key[1], rule=rule, message="", metrics=[key[2]]
                    )
                    for rule in rules
                ]
                for key, rules in self._findings_by_cell().items()
            }

        # Build year -> fund -> key -> entry dict (not just the bare value, so
        # the source, certainty and comment survive to this point)
        year_structured = {}
        for fund, year_data in self._funds().items():
            for year, metrics in year_data.items():
                per_fund = year_structured.setdefault(str(year), {}).setdefault(fund, {})
                for key in all_keys:
                    entry = metrics.get(key)
                    per_fund[key] = entry if isinstance(entry, dict) else None

        wb = Workbook()
        del wb["Sheet"]

        ratios = self._load_ratios(ratio_def_path, key_def_path)

        for year in sorted(year_structured):
            fund_data = year_structured[year]
            funds = sorted(fund_data.keys(), key=display_name)
            # The clean sheet first, since that is the one to work in. The
            # source columns double the width of the sheet and are only
            # needed when tracing a figure back to its page, so they get a
            # sheet of their own rather than sitting between the funds.
            self._write_year_sheet(
                wb, str(year), year, fund_data, funds, display_name, flagged,
                group_order, grouped_keys, with_sources=False, ratios=ratios,
            )
            if self.include_sources:
                self._write_year_sheet(
                    wb, f"{year}{self.SOURCE_SHEET_SUFFIX}", year, fund_data,
                    funds, display_name, flagged, group_order, grouped_keys,
                    with_sources=True, ratios=None,
                )

        self._write_comparison_sheet(wb, display_name)
        self._write_register_sheet(wb, display_name)
        self._write_management_sheet(wb, display_name)
        self._write_legend_sheet(wb, findings or [], resolver)
        wb.save(output_path)
        logger.info(f"Excel file saved to {output_path}")

    def _write_year_sheet(
        self, wb, title, year, fund_data, funds, display_name, flagged,
        group_order, grouped_keys, with_sources: bool, ratios: list | None,
    ):
        ws = wb.create_sheet(title=self._sanitize_sheetname(title))
        self._metric_rows = {}
        column_of = {}

        skipped = self.skipped()
        if skipped:
            # Överst, inte i en fotnot: det här ändrar hur hela bladet ska
            # läsas, och antalet kassor är inte det man tror.
            ws.append([
                f"⚠ {len(skipped)} uppladdat dokument kunde inte analyseras "
                "och saknas nedan. Orsakerna står på fliken Läsanvisning."
                if len(skipped) == 1 else
                f"⚠ {len(skipped)} uppladdade dokument kunde inte analyseras "
                "och saknas nedan. Orsakerna står på fliken Läsanvisning."
            ])
            banner = ws.cell(row=1, column=1)
            banner.font = Font(bold=True)
            banner.fill = PatternFill(
                start_color=self.FLAGGED_FILL, end_color=self.FLAGGED_FILL,
                fill_type="solid",
            )

        header_row = 2 if skipped else 1
        header = ["Nyckeltal"]
        for fund in funds:
            header.append(display_name(fund))
            column_of[fund] = len(header)
            if with_sources:
                header.append("källa")
        ws.append(header)
        for col_num in range(1, len(header) + 1):
            ws.cell(row=header_row, column=col_num).font = Font(bold=True)
        ws.freeze_panes = f"B{header_row + 1}"

        row_idx = header_row + 1
        for group in group_order:
            ws.cell(row=row_idx, column=1, value=group).font = Font(bold=True)
            row_idx += 1

            for key in grouped_keys[group]:
                ws.cell(row=row_idx, column=1, value=key)
                self._metric_rows[key] = row_idx
                col_idx = 2
                for fund in funds:
                    entry = fund_data.get(fund, {}).get(key) or {}
                    value = entry.get(JBGAnnualReportAnalyzer.FIELD_VALUE)
                    certainty = entry.get(JBGAnnualReportAnalyzer.FIELD_CERTAINTY)
                    source = entry.get(JBGAnnualReportAnalyzer.FIELD_SOURCE, "")
                    comment = entry.get(JBGAnnualReportAnalyzer.FIELD_COMMENT, "")

                    cell = ws.cell(row=row_idx, column=col_idx, value=value)
                    problems = flagged.get((fund, str(year), key), [])
                    if problems:
                        # Bara upplysningar: kassan redovisar egna poster
                        # utöver föreskriften, vilket inte är ett räknefel.
                        only_notes = all(
                            getattr(problem, "severity", None)
                            == validation.SEVERITY_INFO
                            for problem in problems
                        )
                        colour = self.INFO_FILL if only_notes else self.FLAGGED_FILL
                        cell.fill = PatternFill(
                            start_color=colour, end_color=colour, fill_type="solid",
                        )
                    else:
                        fill = self._certainty_fill(certainty)
                        if fill is not None:
                            cell.fill = fill

                    note = self._build_note(certainty, source, comment, problems)
                    if note:
                        # Cell notes keep the model's reasoning available on
                        # hover without adding three columns per fund.
                        cell.comment = Comment(note, "Årsredovisningsgranskning")
                        cell.comment.width = 380
                        cell.comment.height = 180
                    col_idx += 1

                    if with_sources:
                        ws.cell(row=row_idx, column=col_idx, value=source)
                        col_idx += 1
                row_idx += 1

        row_idx = self._write_ratio_rows(
            ws, row_idx + 1, ratios or [], funds, fund_data, column_of
        )
        self._autosize(ws)

    @staticmethod
    def _build_note(certainty, source, comment, problems) -> str:
        parts = []
        if certainty:
            parts.append(f"Säkerhet: {certainty}")
        if source:
            parts.append(f"Källa: {source}")
        if comment:
            parts.append(f"Kommentar: {comment}")
        for problem in problems:
            parts.append(f"⚠ {problem.rule}: {problem.message}")
        return "\n\n".join(parts)

    @staticmethod
    def _autosize(ws) -> None:
        for col in ws.columns:
            letter = get_column_letter(col[0].column)
            longest = max(
                (len(str(cell.value)) for cell in col if cell.value is not None),
                default=0,
            )
            ws.column_dimensions[letter].width = max(8, min(longest + 2, 40))

    def _write_register_sheet(self, wb, display_name) -> None:
        """Anmärkningar som kommit tillbaka, det envisaste först.

        Skillnaden mellan brus och mönster avgör vad som är värt att åtgärda,
        och den syns inte i en enskild körning. Alfa-kassans avvikelse på
        70 691 kom tillbaka fem gånger innan någon kände igen den; Visions
        på 99 670 kom en gång och aldrig mer.

        Bara det som återkommit listas. En engångsföreteelse står redan på
        årtalsfliken.
        """
        entries = [
            entry for entry in findings_register.summary(history.history_dir())
            if entry.get("antal", 0) >= self.MIN_OCCURRENCES_LISTED
        ]
        if not entries:
            return

        ws = wb.create_sheet(title=self.REGISTER_SHEET)
        ws.append(["Gånger", "Kassa", "År", "Kontroll", "Nyckeltal",
                   "Först sedd", "Senast sedd", "Senaste lydelse"])
        for column in range(1, 9):
            ws.cell(row=1, column=column).font = Font(bold=True)
        ws.freeze_panes = "A2"

        for entry in entries:
            ws.append([
                entry.get("antal", 0),
                display_name(entry.get("kassa", "")),
                entry.get("ar", ""),
                entry.get("kontroll", ""),
                ", ".join(entry.get("nyckeltal") or []),
                entry.get("forst", ""),
                entry.get("senast", ""),
                entry.get("senaste_meddelande", ""),
            ])

        for column, width in zip("ABCDEFGH", (8, 26, 7, 34, 34, 12, 12, 70),
                                 strict=False):
            ws.column_dimensions[column].width = width
        for row in ws.iter_rows(min_row=2):
            row[-1].alignment = Alignment(wrap_text=True, vertical="top")

    def _write_comparison_sheet(self, wb, display_name) -> None:
        """Varje rörelse mellan år över det dubbla, störst först.

        Inget allvarlighetsbegrepp och ingen färg: trösklarna visade att en
        rörelse på tio gånger oftast är verklig, så en anmärkning hade varit
        fel. Men GS a-kassas "Antal ersättningsdagar 22 286 -> 306 045" är en
        felläsning på 13,7 gånger, och den syns direkt när allt står sorterat.
        """
        movements = comparison.compare(self._funds())
        if not movements:
            return

        ws = wb.create_sheet(title=self.COMPARISON_SHEET)
        ws.append([
            "Kassa", "Nyckeltal", "Från år", "Till år", "Före", "Efter",
            "Gånger", "Tecken",
        ])
        for column in range(1, 9):
            ws.cell(row=1, column=column).font = Font(bold=True)
        ws.freeze_panes = "A2"

        for move in movements:
            ws.append([
                display_name(move["kassa"]), move["nyckeltal"],
                move["fran_ar"], move["till_ar"], move["fran"], move["till"],
                round(move["kvot"], 1), "byter tecken" if move["teckenbyte"] else "",
            ])
            if move["kvot"] >= comparison.FINDING_RATIO:
                # Så stora rörelser är sällan verkliga. De är också
                # anmärkningar i egen rätt, men raden ska synas här med.
                for column in range(1, 9):
                    ws.cell(row=ws.max_row, column=column).fill = PatternFill(
                        start_color=self.FLAGGED_FILL,
                        end_color=self.FLAGGED_FILL, fill_type="solid",
                    )

        for column, width in zip("ABCDEFGH", (28, 52, 9, 9, 16, 16, 9, 14),
                                 strict=False):
            ws.column_dimensions[column].width = width

    def _write_management_sheet(self, wb, display_name) -> None:
        """Kassorna på rader, sammanfattning och stödcitat i par av kolumner.

        Citatet står bredvid den sammanfattning det stöder, inte samlat i en
        egen spalt längst ut: den som läser en sammanfattning ska kunna
        kontrollera den utan att leta.
        """
        summaries = self.management_summaries()
        if not summaries:
            return

        ws = wb.create_sheet(title=self.MANAGEMENT_SHEET)
        header = ["Kassa"]
        for topic in management_report.TOPICS:
            header += [topic["heading"], "Citat som stöder", "Sida"]
        ws.append(header)
        for column in range(1, len(header) + 1):
            ws.cell(row=1, column=column).font = Font(bold=True)
        ws.freeze_panes = "B2"

        for fund in sorted(summaries, key=display_name):
            row = [display_name(fund)]
            for topic in management_report.TOPICS:
                entry = (summaries.get(fund) or {}).get(topic["key"]) or {}
                row += [
                    entry.get("sammanfattning", ""),
                    entry.get("citat", ""),
                    entry.get("sida") or "",
                ]
            ws.append(row)

            # Ett ungefärligt citat märks, inte stryks. Läsaren ska kunna
            # bedöma det själv.
            for index, topic in enumerate(management_report.TOPICS):
                entry = (summaries.get(fund) or {}).get(topic["key"]) or {}
                if entry.get("citat_kontroll") != management_report.QUOTE_APPROXIMATE:
                    continue
                cell = ws.cell(row=ws.max_row, column=3 + index * 3)
                cell.fill = PatternFill(
                    start_color=self.FLAGGED_FILL, end_color=self.FLAGGED_FILL,
                    fill_type="solid",
                )
                cell.comment = Comment(
                    "Citatet återfanns inte ordagrant i dokumentet, men har "
                    f"stöd till {entry.get('citat_stod', 0):.0%}. Vanligt när "
                    "sidan är inskannad: modellen skriver av en felläst text "
                    "rättstavat. Kontrollera mot källan.",
                    "JBG nyckeltalsanalys",
                )
                cell.comment.width = 380

        widths = [28] + [60, 48, 6] * len(management_report.TOPICS)
        for index, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(index)].width = width
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    def _write_legend_sheet(self, wb, findings: list, resolver) -> None:
        """A short reading guide, plus any sanity checks that failed."""
        ws = wb.create_sheet(title="Läsanvisning")
        ws.append(["Färgkodning av värden (modellens egen bedömning)"])
        ws["A1"].font = Font(bold=True)
        for _, colour, label in self.CERTAINTY_BANDS:
            ws.append([label])
            ws.cell(row=ws.max_row, column=1).fill = PatternFill(
                start_color=colour, end_color=colour, fill_type="solid"
            )
        ws.append(["Ingår i en misslyckad rimlighetskontroll"])
        ws.cell(row=ws.max_row, column=1).fill = PatternFill(
            start_color=self.FLAGGED_FILL, end_color=self.FLAGGED_FILL, fill_type="solid"
        )
        ws.append([
            "Upplysning: summan överstiger de delposter föreskriften räknar "
            "upp, sannolikt för att kassan redovisar egna poster. Inget fel."
        ])
        ws.cell(row=ws.max_row, column=1).fill = PatternFill(
            start_color=self.INFO_FILL, end_color=self.INFO_FILL, fill_type="solid"
        )
        ws.append([])
        ws.append(["Håll pekaren över ett värde för källa, säkerhet och kommentar."])
        skipped = self.skipped()
        if skipped:
            ws.append([])
            ws.append([f"Ej analyserade dokument ({len(skipped)})"])
            heading = ws.cell(row=ws.max_row, column=1)
            heading.font = Font(bold=True)
            heading.fill = PatternFill(
                start_color=self.FLAGGED_FILL, end_color=self.FLAGGED_FILL,
                fill_type="solid",
            )
            ws.append([
                "Dessa filer laddades upp men ingår inte i sammanställningen. "
                "Kassorna saknas alltså helt i årtalsfliken."
            ])
            ws.append(["Fil", "Orsak"])
            for column in (1, 2):
                ws.cell(row=ws.max_row, column=column).font = Font(bold=True)
            for entry in skipped:
                ws.append([
                    str(entry.get("fil", "")), str(entry.get("orsak", ""))
                ])

        summary = self.data.get(self.SUMMARY_KEY) or {}
        if summary:
            ws.append([])
            ws.append(["Körningen"])
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
            uploaded = summary.get("uppladdade_dokument", 0)
            skipped_count = summary.get("ej_analyserade", 0)
            combinations = summary.get("kassa_ar_kombinationer", 0)
            ws.append([f"Uppladdade dokument: {uploaded}"])
            ws.append([f"Ej analyserade: {skipped_count}"])
            ws.append([f"Kombinationer av kassa och år i utdata: {combinations}"])
            if combinations != uploaded - skipped_count:
                # En kassa som tappats på vägen syns annars bara genom att
                # någon råkar räkna kolumner.
                ws.append([
                    "⚠ Antalet stämmer inte: något dokument har hamnat på "
                    "samma kassa och år som ett annat, eller gett fler än "
                    "ett år. Kontrollera loggen."
                ])
                ws.cell(row=ws.max_row, column=1).fill = PatternFill(
                    start_color=self.FLAGGED_FILL, end_color=self.FLAGGED_FILL,
                    fill_type="solid",
                )

        ws.append([])
        ws.append(["Flikar"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        ws.append([
            "Årtalsfliken innehåller uppgifterna från årsredovisningarna och, "
            "längst ned, nyckeltalsberäkningar. Det är fliken att arbeta i."
        ])
        ws.append([
            "Fliken \"med källa\" innehåller samma uppgifter med en källkolumn "
            "per kassa, för den som vill härleda ett värde till rätt sida."
        ])
        ws.append([])
        ws.append(["Nyckeltalsberäkningar"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        ws.append([
            "Beräkningarna är formler som hänvisar till raderna ovanför, inte "
            "fasta tal. Rättas ett värde räknas nyckeltalet om direkt. Håll "
            "pekaren över namnet för vad beräkningen avser."
        ])
        ws.append([
            "En tom cell betyder att någon ingående uppgift saknas för kassan; "
            "kommentaren på cellen anger vilken."
        ])
        ws.append([])

        ws.append(["Rimlighetskontroller"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        if not findings:
            ws.append(["Inga anmärkningar."])
        else:
            ws.append(["Kassa", "År", "Kontroll", "Anmärkning"])
            for col in range(1, 5):
                ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
            for finding in findings:
                ws.append([
                    resolver.short_name(finding.fund) if resolver else finding.fund,
                    finding.year,
                    finding.rule,
                    finding.message,
                ])
        self._autosize(ws)

    def _sanitize_sheetname(self, name: str) -> str:
        # Excel sheet names max 31 chars and cannot contain some symbols
        return name[:31].replace("/", "-").replace("\\", "-").replace(":", "-")


