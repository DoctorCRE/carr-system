import test from "node:test";
import assert from "node:assert/strict";
import {
  deliverableMime,
  DELIVERABLE_POLICY,
  verifyDeliverableBytes,
} from "../src/deal-deliverables.js";
import { createHash } from "node:crypto";
const pdf = new TextEncoder().encode("%PDF-1.7\nsynthetic\n%%EOF");
const hash = (b) => createHash("sha256").update(b).digest("hex");
test("content validation accepts PDF and common images without trusting extensions", () => {
  assert.equal(deliverableMime(pdf), "application/pdf");
  assert.equal(
    deliverableMime(Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10])),
    "image/png",
  );
  assert.equal(
    deliverableMime(Uint8Array.from([255, 216, 255, 224])),
    "image/jpeg",
  );
  assert.equal(
    deliverableMime(new TextEncoder().encode("RIFFxxxxWEBPVP8 ")),
    "image/webp",
  );
  assert.equal(
    deliverableMime(
      Uint8Array.from([
        0,
        0,
        0,
        24,
        ...new TextEncoder().encode("ftypheic00000000"),
      ]),
    ),
    "image/heic",
  );
  assert.throws(
    () => deliverableMime(new TextEncoder().encode("<html>fake.pdf</html>")),
    /unsupported_mime/,
  );
  assert.throws(
    () =>
      deliverableMime(new TextEncoder().encode("PK\x03\x04word/document.xml")),
    /unsupported_mime/,
  );
});
test("HEIC content accepts compatible HEIC brands and refuses unrelated ISO media", () => {
  const container = (brand) =>
    new Uint8Array([
      0,
      0,
      0,
      24,
      ...new TextEncoder().encode(`ftypmif10000${brand}mif1`),
    ]);
  assert.equal(deliverableMime(container("heic")), "image/heic");
  assert.throws(() => deliverableMime(container("avif")), /unsupported_mime/);
});
test("byte verification refuses size, MIME and SHA mismatches", async () => {
  const receipt = {
    bytes: pdf.length,
    sha256: hash(pdf),
    mime: "application/pdf",
  };
  await verifyDeliverableBytes(pdf, receipt);
  await assert.rejects(
    verifyDeliverableBytes(pdf, { ...receipt, bytes: 1 }),
    /upload_size_refused/,
  );
  await assert.rejects(
    verifyDeliverableBytes(pdf, { ...receipt, sha256: "0".repeat(64) }),
    /hash_mismatch/,
  );
  await assert.rejects(
    verifyDeliverableBytes(pdf, { ...receipt, mime: "image/png" }),
    /unsupported_mime/,
  );
  assert.equal(DELIVERABLE_POLICY.maxBytes, 25 * 1024 * 1024);
  await assert.rejects(
    verifyDeliverableBytes(
      new Uint8Array(DELIVERABLE_POLICY.maxBytes + 1),
      receipt,
    ),
    /upload_size_refused/,
  );
});

function officeZip(member) {
  const local = [],
    central = [];
  let offset = 0;
  for (const name of ["[Content_Types].xml", member]) {
    const n = Buffer.from(name),
      header = Buffer.alloc(30),
      entry = Buffer.alloc(46);
    header.writeUInt32LE(0x04034b50);
    header.writeUInt16LE(n.length, 26);
    entry.writeUInt32LE(0x02014b50);
    entry.writeUInt16LE(n.length, 28);
    entry.writeUInt32LE(offset, 42);
    local.push(header, n);
    central.push(entry, n);
    offset += header.length + n.length;
  }
  const directory = Buffer.concat(central),
    end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50);
  end.writeUInt16LE(2, 8);
  end.writeUInt16LE(2, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}
test("Office types require compound content or structured OOXML ZIP package members", () => {
  for (const [member, mime] of [
    [
      "word/document.xml",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    [
      "xl/workbook.xml",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ],
    [
      "ppt/presentation.xml",
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
  ]) {
    assert.equal(deliverableMime(officeZip(member)), mime);
  }
  assert.throws(
    () => deliverableMime(officeZip("unrelated.txt")),
    /unsupported_mime/,
  );
  for (const [marker, mime] of [
    ["WordDocument", "application/msword"],
    ["Workbook", "application/vnd.ms-excel"],
    ["PowerPoint Document", "application/vnd.ms-powerpoint"],
  ]) {
    const b = Buffer.alloc(1024);
    b.set([208, 207, 17, 224, 161, 177, 26, 225]);
    b.set(Buffer.from(marker + "\0", "utf16le"), 512);
    assert.equal(deliverableMime(b), mime);
  }
  const malformed = officeZip("word/document.xml");
  malformed.writeUInt32LE(0xffffffff, malformed.length - 6);
  assert.throws(() => deliverableMime(malformed), /unsupported_mime/);
});
