// CARR Pipeline panel — Premiere Pro UXP scaffold (v0.1, not yet loaded/tested).
// Load via Adobe UXP Developer Tool once installed: Add Plugin -> this folder -> Load.
// API docs: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/
// NOTE: verify exact API shapes against the docs when first loading — the
// premierepro UXP API is new and method signatures may shift between dot releases.

const PIPE = "/Users/booko/Movies/CARR Video Pipeline";

function status(msg) {
    document.getElementById("status").textContent = msg;
}

async function getProject() {
    const ppro = require("premierepro");
    const project = await ppro.Project.getActiveProject();
    if (!project) throw new Error("No project open in Premiere.");
    return { ppro, project };
}

async function importFromDrops() {
    const fs = require("fs");
    const { project } = await getProject();
    const exts = /\.(mp4|mov|m4v|wav|mp3|png|jpg)$/i;
    let paths = [];
    for (const dir of ["01_Drop_Horizontal", "02_Drop_Vertical"]) {
        const full = `${PIPE}/${dir}`;
        for (const f of fs.readdirSync(full)) {
            if (exts.test(f)) paths.push(`${full}/${f}`);
        }
    }
    if (!paths.length) return status("Drop folders are empty.");
    await project.importFiles(paths, true);
    status(`Imported ${paths.length} file(s).`);
}

// Add markers to the active sequence from Scripts/markers.csv ("seconds,label" per line)
async function markersFromCsv() {
    const fs = require("fs");
    const { ppro, project } = await getProject();
    const seq = await project.getActiveSequence();
    if (!seq) return status("No active sequence.");
    const csv = fs.readFileSync(`${PIPE}/Scripts/markers.csv`, "utf-8");
    const markers = await ppro.Markers.getMarkers(seq);
    let n = 0;
    for (const line of csv.split("\n")) {
        const [sec, ...label] = line.split(",");
        if (!sec || isNaN(parseFloat(sec))) continue;
        await markers.createMarker(parseFloat(sec), label.join(",").trim() || "marker");
        n++;
    }
    status(`Added ${n} marker(s) to "${await seq.name}".`);
}

async function sequenceInfo() {
    const { project } = await getProject();
    const seq = await project.getActiveSequence();
    if (!seq) return status("No active sequence.");
    status(`Active sequence: ${await seq.name}`);
}

function wire(id, fn) {
    document.getElementById(id).addEventListener("click", () =>
        fn().catch(e => status("Error: " + e.message)));
}

document.addEventListener("DOMContentLoaded", () => {
    wire("btnImport", importFromDrops);
    wire("btnMarkers", markersFromCsv);
    wire("btnInfo", sequenceInfo);
});
