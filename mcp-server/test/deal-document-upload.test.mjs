import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyUploadBytes } from '../src/deal-document-upload.ts';
import { createHash } from 'node:crypto';
test('admit matching photo signatures and refuse Office ZIP, HTML, SVG and mismatched extensions',async()=> {
 const jpeg=Uint8Array.from([255,216,255,0,255,217]);
 const png=new Uint8Array(45);png.set([137,80,78,71,13,10,26,10]);png.set(new TextEncoder().encode('IHDR'),12);png.set(new TextEncoder().encode('IEND'),37);
 const heic=new Uint8Array(24);new DataView(heic.buffer).setUint32(0,24);heic.set(new TextEncoder().encode('ftypheic'),4);
 const heif=heic.slice();heif.set(new TextEncoder().encode('mif1'),8);
 for(const [bytes,mime,filename] of [[jpeg,'image/jpeg','photo.jpg'],[png,'image/png','photo.png'],[heic,'image/heic','photo.heic'],[heif,'image/heif','photo.heif']]) {
   const row={filename,declared_mime:mime,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
   await verifyUploadBytes(bytes,row);
   await assert.rejects(verifyUploadBytes(bytes,{...row,filename:'photo.pdf'}),/upload_format_refused/);
 }
 for(const content of ['<svg/>','<html/>','PK\x03\x04word/document.xml']) {
   const bytes=new TextEncoder().encode(content);
   await assert.rejects(verifyUploadBytes(bytes,{filename:'LOI.pdf',declared_mime:'application/pdf',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}),/upload_format_refused/);
 }
});
test('partner_pdf_bytes_stage_only_for_reserved_actor_deal_and_hash', async () => {
  const bytes = new TextEncoder().encode('%PDF-1.7\nsynthetic\n%%EOF');
  const metadata = { filename: 'LOI.pdf', declared_mime: 'application/pdf', bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex') };
  await verifyUploadBytes(bytes, metadata);
  await assert.rejects(verifyUploadBytes(bytes, { ...metadata, sha256: '0'.repeat(64) }), /upload_hash_mismatch/);
});
