import json
import pandas as pd
from pathlib import Path
from typing import Union
import logging
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

class JsonConverter:
    def __init__(self, json_path: Union[str, Path], include_sources: bool = False):
        self.json_path = Path(json_path)
        if not self.json_path.exists():
            raise FileNotFoundError(f"JSON file not found: {self.json_path}")
        self.include_sources = include_sources
        self.data = self._load_json()

    def _load_json(self):
        with open(self.json_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def to_dataframe(self) -> pd.DataFrame:
        """
        Converts nested JSON structure to a flat DataFrame with columns:
        Fund | Year | Key | Value | Source (optional)
        """
        rows = []
        for fund_name, years in self.data.items():
            for year, key_numbers in years.items():
                for key, value_dict in key_numbers.items():
                    value = value_dict.get(JBGAnnualReportAnalyzer.FIELD_VALUE)
                    if self.include_sources:
                        source = value_dict.get(JBGAnnualReportAnalyzer.FIELD_SOURCE)
                        rows.append({
                            "Fund": fund_name,
                            "Year": year,
                            "Key": key,
                            "Value": value,
                            "Source": source
                        })
                    else:
                        rows.append({
                            "Fund": fund_name,
                            "Year": year,
                            "Key": key,
                            "Value": value
                        })
                        
        return pd.DataFrame(rows)

    def to_csv(self, output_path: Union[str, Path]):
        df = self.to_dataframe()
        output_path = Path(output_path)
        df.to_csv(output_path, index=False, encoding="utf-8-sig", sep=";")
        logger.info(f"CSV file saved to {output_path}")

    def to_excel(self, output_path: Union[str, Path], by: str = "fund"):
        """
        Save to Excel with multiple sheets.
        by: 'fund' or 'year'
        """
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

    def to_excel_by_year(
        self,
        output_path: Union[str, Path],
        key_def_path: Union[str, Path],
        fund_names: Union[None, str, Path] = None
    ):
        """
        Export JSON data to Excel with:
        - One sheet per year
        - Funds as columns
        - Nyckeltal as rows, grouped and ordered by key_def_path
        - Optionally, the source is given
        """
        # Load key definitions with groups
        with open(key_def_path, encoding="utf-8") as f:
            key_defs = json.load(f)
            
        # Load name mapping if provided
        fund_name_map = {}
        if fund_names:
            with open(fund_names, encoding="utf-8") as f:
                name_data = json.load(f)
                fund_name_map = {
                    entry["Officiellt namn"]: entry["Kort namn"]
                    for entry in name_data
                }

        # Prepare group structure
        grouped_keys = {}
        for entry in key_defs:
            group = entry.get("Grupp", "🧩 Övrigt")
            grouped_keys.setdefault(group, []).append(entry["Nyckeltal"])
        group_order = list(grouped_keys.keys())

        # Build year -> { fund -> { key -> value } }
        year_structured = {}
        for fund, year_data in self.data.items():
            for year, metrics in year_data.items():
                if year not in year_structured:
                    year_structured[year] = {}
                for key in [entry["Nyckeltal"] for entry in key_defs]:
                    value = metrics.get(key, {}).get(JBGAnnualReportAnalyzer.FIELD_VALUE) if key in metrics else None
                    if self.include_sources:
                        source = metrics.get(key, {}).get(JBGAnnualReportAnalyzer.FIELD_SOURCE) if key in metrics else None
                    year_structured[year].setdefault(fund, {})[key] = value

        # Start workbook
        wb = Workbook()
        del wb["Sheet"]

        for year, fund_data in year_structured.items():
            ws = wb.create_sheet(title=str(year))
            funds = sorted(fund_data.keys())

            # Header row
            header = ["Nyckeltal"]
            for fund in funds:
                short_name = fund_name_map.get(fund, fund)  # Fallback to original if no match
                header.append(short_name)
                if self.include_sources:
                    header.append(f"⬅️ källa")
            ws.append(header)
            
            # Ensure header has bold font
            ws.row_dimensions[1].font = Font(bold=True)
            for col_num in range(1, len(header) + 1):
                ws.cell(row=1, column=col_num).font = Font(bold=True)

            row_idx = 2
            for group in group_order:
                ws.cell(row=row_idx, column=1, value=group).font = Font(bold=True)
                row_idx += 1

                for key in grouped_keys[group]:
                    row = [key]
                    for fund in funds:
                        value = fund_data.get(fund, {}).get(key, None)
                        row.append(value)
                        if self.include_sources:
                            source = self.data[fund][year].get(key, {}).get(JBGAnnualReportAnalyzer.FIELD_SOURCE, "")
                            row.append(source)

                    for col_idx, val in enumerate(row, start=1):
                        ws.cell(row=row_idx, column=col_idx, value=val)
                    row_idx += 1

            # Adjust column widths
            for col in range(1, len(funds) + 2):
                col_letter = get_column_letter(col)
                ws.column_dimensions[col_letter].width = 25 if col == 1 else 15

            # Adjust all column widths depening on longest string inside
            for col in ws.columns:
                max_length = 0
                column = col[0].column  # Get numerical index (1-based)
                column_letter = get_column_letter(column)
                for cell in col:
                    try:
                        if cell.value is not None:
                            max_length = max(max_length, len(str(cell.value)))
                    except:
                        pass
                adjusted_width = max(8, min(max_length + 2, 40)) # Add padding but avoid extremes
                ws.column_dimensions[column_letter].width = adjusted_width
        
        # Save book and finish up
        wb.save(output_path)
        logger.info(f"Excel file saved to {output_path}")

    def _sanitize_sheetname(self, name: str) -> str:
        # Excel sheet names max 31 chars and cannot contain some symbols
        return name[:31].replace("/", "-").replace("\\", "-").replace(":", "-")
    

