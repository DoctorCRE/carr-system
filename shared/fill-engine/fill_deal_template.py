#!/usr/bin/env python3
# Fill a deal spreadsheet template with supplied inputs.
import sys, json, subprocess, os, shutil, tempfile, openpyxl
def fill(template, out, inputs):
    wb = openpyxl.load_workbook(template)
    ws = wb.active
    for cell, val in inputs.items():
        ws[cell] = val
    wb.calculation.fullCalcOnLoad = True
    indir = tempfile.mkdtemp(prefix="dfin_")
    outdir = tempfile.mkdtemp(prefix="dfout_")
    src = os.path.join(indir, "book.xlsx")
    wb.save(src)
    subprocess.run(
        ["libreoffice","--headless","--calc","--convert-to",
         "xlsx:Calc MS Excel 2007 XML","--outdir", outdir, src],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "HOME":"/tmp"})
    shutil.move(os.path.join(outdir, "book.xlsx"), out)
    shutil.rmtree(indir, ignore_errors=True); shutil.rmtree(outdir, ignore_errors=True)
    return out
if __name__ == "__main__":
    fill(sys.argv[1], sys.argv[2], json.load(open(sys.argv[3])))
    print("wrote", sys.argv[2])
