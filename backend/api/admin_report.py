"""Admin-key-protected Excel export built with Python's standard library."""
import io
import os
import zipfile
import xml.etree.ElementTree as ET
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from backend import db

router = APIRouter(prefix="/api/admin")

def _column(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name

def _workbook(rows):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace("", ns)
    sheet = ET.Element(f"{{{ns}}}worksheet")
    data = ET.SubElement(sheet, f"{{{ns}}}sheetData")
    for r, values in enumerate(rows, 1):
        row = ET.SubElement(data, f"{{{ns}}}row", {"r": str(r)})
        for c, value in enumerate(values, 1):
            cell = ET.SubElement(row, f"{{{ns}}}c", {"r": f"{_column(c)}{r}"})
            if isinstance(value, (int, float)):
                ET.SubElement(cell, f"{{{ns}}}v").text = str(value)
            else:
                cell.set("t", "inlineStr")
                ET.SubElement(ET.SubElement(cell, f"{{{ns}}}is"), f"{{{ns}}}t").text = str(value)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Stock and sales" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet, encoding="utf-8", xml_declaration=True))
    output.seek(0)
    return output

@router.get("/stock-sales.xlsx")
def stock_sales_report(x_admin_key: str | None = Header(default=None)):
    key = os.environ.get("MEDGUARD_ADMIN_KEY")
    if not key:
        raise HTTPException(503, "Admin report is disabled. Set MEDGUARD_ADMIN_KEY before starting the app.")
    if not x_admin_key or not __import__("hmac").compare_digest(key, x_admin_key):
        raise HTTPException(403, "Admin access required.")
    with db.connect() as connection:
        rows = connection.execute("""SELECT h.name pharmacy,m.name medicine,m.unit,
          COALESCE((SELECT SUM(s.quantity) FROM sales_transactions s WHERE s.hospital_id=h.id AND s.medicine_id=m.id),0) AS units_sold,
          i.current_stock
          FROM inventory i JOIN hospitals h ON h.id=i.hospital_id JOIN medicines m ON m.id=i.medicine_id
          ORDER BY h.name,m.name""").fetchall()
    generated = db.now_iso()
    values = [["Pharmacy", "Medicine", "Unit", "Units sold (recorded)", "Units remaining in stock", "Updated (UTC)"]]
    values += [[r["pharmacy"],r["medicine"],r["unit"],r["units_sold"],r["current_stock"],generated] for r in rows]
    return StreamingResponse(_workbook(values), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers={"Content-Disposition":"attachment; filename=medguard_admin_stock_sales.xlsx","Cache-Control":"no-store"})
