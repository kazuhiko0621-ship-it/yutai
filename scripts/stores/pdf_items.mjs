// PDFの文字を位置つきで書き出す（クリエイト・レストランツの優待利用可能店舗一覧の読み取り用）
// 使い方: node pdf_items.mjs <PDFファイル> <出力JSON>  → [{p, x, y, s}, ...]
// （pdf.jsの警告が標準出力に混ざるため、結果はファイルに書く）
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";

const require = createRequire(import.meta.url);
const base = require.resolve("pdfjs-dist/package.json").replace(/package\.json$/, "");
const data = new Uint8Array(readFileSync(process.argv[2]));
const doc = await pdfjs.getDocument({ data, cMapUrl: base + "cmaps/", cMapPacked: true,
  standardFontDataUrl: base + "standard_fonts/", isEvalSupported: false, useSystemFonts: false, verbosity: 0 }).promise;
const out = [];
for (let p = 1; p <= doc.numPages; p++) {
  const tc = await (await doc.getPage(p)).getTextContent();
  for (const it of tc.items) {
    if (it.str && it.str.trim()) out.push({ p, x: Math.round(it.transform[4]), y: Math.round(it.transform[5]), s: it.str });
  }
}
writeFileSync(process.argv[3], JSON.stringify(out));
