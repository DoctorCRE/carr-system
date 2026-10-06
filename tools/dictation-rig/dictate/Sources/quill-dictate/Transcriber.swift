// Transcriber.swift — one utterance through the SHARED engine.
//
// Same whisper-cli, same ggml-large-v3-turbo, same CARR vocab prompt as
// meeting mode's transcribe_session.py. The engine choice was ruled once
// (decision 28e35509 / Joe's "most effective" model call) and this mode
// inherits it wholesale — that has NOT changed with the resident final
// server below, which runs the identical model, just kept warm.
//
// SERVER-FIRST PATH (added 2026-08-08): whisper-cli reloads the 1.6GB model
// on every spawn — a ~14s Metal cold start on the first call after any
// restart, repeatable every call after that too since whisper-cli never
// stays resident. WhisperServer's FINAL role (see that file) keeps the same
// model loaded in a resident process instead. transcribe(wav:) tries that
// server first when final_server=="auto", and falls back to the whisper-cli
// path below on ANY failure — server not running, timeout, unparseable
// response (see WhisperServer.postFinalInference's own header for how it
// handles a still-loading server without either failing fast or hanging
// forever). This function intentionally does NOT check any in-process "is
// the server spawned" flag: it has none to check. It must also work
// standalone from the CLI (`quill-dictate transcribe`, main.swift) with no
// App-owned WhisperServer in the process at all, so the presence or absence
// of a listening server on final_server_port — discovered by just trying
// the request — IS the spawned/not-spawned signal for this function's
// purposes, exactly as it would be for anyone else talking to it over
// loopback HTTP.
import Foundation

struct Transcriber {
    let config: Config
    let finalServer: WhisperServer?

    struct Failure: Error { let message: String }

    init(config: Config, finalServer: WhisperServer? = nil) {
        self.config = config
        self.finalServer = finalServer
    }

    /// Blocking; run on a background queue. Returns cleaned text ("" when
    /// whisper heard nothing worth keeping).
    func transcribe(wav: URL) throws -> String {
        if config.finalServer == "auto" {
            if let serverText = tryServerTranscribe(wav: wav) {
                return Transcriber.clean(serverText)
            }
            Log.shared.line("FINAL server unavailable, cli fallback")
            if let finalServer {
                return try finalServer.withFinalServerStoppedForRecovery(
                    reason: "inference request failed"
                ) {
                    try transcribeViaCli(wav: wav)
                }
            }
        }
        return try transcribeViaCli(wav: wav)
    }

    /// Reads the WAV and POSTs it to the resident final-pass server. Returns
    /// nil on ANY failure (unreadable file, connection refused, timeout,
    /// unparseable JSON) — the caller's only reaction to nil is the existing
    /// whisper-cli path, unchanged.
    ///
    /// FAST-FAIL PRE-CHECK: postFinalInference's connect phase deliberately
    /// waits out a real cold start rather than failing fast (see its own
    /// header) — correct for "the server is starting" but wrong for "the
    /// server was never going to start," which would otherwise pay that same
    /// wait on EVERY utterance. Checking the same binary/model existence
    /// WhisperServer.start() already gates on catches that case for free —
    /// if the resident server could never have launched, nothing is
    /// listening now or later, so skip straight to whisper-cli instead of
    /// waiting to discover that the hard way.
    private func tryServerTranscribe(wav: URL) -> String? {
        guard FileManager.default.isExecutableFile(atPath: WhisperServer.binaryPath),
              FileManager.default.fileExists(atPath: config.modelPath) else { return nil }
        guard let wavData = try? Data(contentsOf: wav) else { return nil }
        return WhisperServer.postFinalInference(port: config.finalServerPort,
                                                  wavData: wavData,
                                                  prompt: vocabPrompt,
                                                  timeoutSeconds: 30.0)
    }

    private func transcribeViaCli(wav: URL) throws -> String {
        let model = FileManager.default.fileExists(atPath: config.modelPath)
            ? config.modelPath : config.fallbackModelPath
        guard FileManager.default.fileExists(atPath: model) else {
            throw Failure(message: "no whisper model at \(config.modelPath) or fallback")
        }
        guard FileManager.default.fileExists(atPath: config.whisperCli) else {
            throw Failure(message: "whisper-cli not found at \(config.whisperCli)")
        }

        let outBase = wav.deletingPathExtension().path
        var args = ["-m", model, "-f", wav.path,
                    "-l", "en", "-nt", "-np", "-otxt", "-of", outBase]
        if let prompt = vocabPrompt {
            args += ["--prompt", prompt]
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.whisperCli)
        process.arguments = args
        // Capture diagnostics in a real file. Waiting for a chatty process to
        // exit before reading an attached Pipe can deadlock on the same 16 KiB
        // back-pressure that wedged the resident servers.
        let diagnosticURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("quill-whisper-cli-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: diagnosticURL.path, contents: nil)
        guard let diagnosticHandle = FileHandle(forWritingAtPath: diagnosticURL.path) else {
            throw Failure(message: "could not create whisper-cli diagnostic log")
        }
        defer { try? FileManager.default.removeItem(at: diagnosticURL) }
        process.standardOutput = diagnosticHandle
        process.standardError = diagnosticHandle
        try process.run()
        process.waitUntilExit()
        try? diagnosticHandle.close()
        let noise = (try? String(contentsOf: diagnosticURL, encoding: .utf8)) ?? ""
        guard process.terminationStatus == 0 else {
            throw Failure(message: "whisper-cli rc=\(process.terminationStatus): \(noise.suffix(300))")
        }

        let txtPath = outBase + ".txt"
        defer { try? FileManager.default.removeItem(atPath: txtPath) }
        let raw = (try? String(contentsOfFile: txtPath, encoding: .utf8)) ?? ""
        return Transcriber.clean(raw)
    }

    /// The CARR vocab prompt, trimmed, or nil when missing/blank — read the
    /// same way for both the cli path's --prompt flag and the server path's
    /// "prompt" form field, so a degraded vocab bias on one path can never
    /// silently diverge from the other.
    ///
    /// vocab-prompt.txt is tracked and carries only generic industry terms
    /// and place names (WR-000049: this repo is public, no client roster in
    /// the tracked tree). vocab-local.txt, gitignored, sits beside it as the
    /// per-machine layer with real client/practice/person names — loaded and
    /// appended when present, same as transcribe_session.py's load_prompt(),
    /// so both engines can never silently diverge on vocab bias. Absent is
    /// not an error: a fresh clone has no local vocab file yet.
    private var vocabPrompt: String? {
        guard let prompt = try? String(contentsOfFile: config.vocabPromptPath, encoding: .utf8) else { return nil }
        var trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let localPath = (config.vocabPromptPath as NSString)
            .deletingLastPathComponent
            .appending("/vocab-local.txt")
        if let local = try? String(contentsOfFile: localPath, encoding: .utf8) {
            let localTrimmed = local.trimmingCharacters(in: .whitespacesAndNewlines)
            if !localTrimmed.isEmpty {
                trimmed = trimmed.isEmpty ? localTrimmed : "\(trimmed) \(localTrimmed)"
            }
        }
        return trimmed.isEmpty ? nil : trimmed
    }

    /// Collapse whisper's line breaks into a single utterance and drop the
    /// known hallucination tokens it emits on silence-adjacent audio.
    static func clean(_ raw: String) -> String {
        let junk: Set<String> = [
            "[BLANK_AUDIO]", "[ Silence ]", "[silence]", "(silence)",
            "[MUSIC]", "[music]", "(music)", "[SOUND]", "[NOISE]",
        ]
        let joined = raw
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !junk.contains($0) }
            .joined(separator: " ")
        return joined.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
