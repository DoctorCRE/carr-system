// Define Chromium arguments that isolate test browser credential storage.
export const CHROMIUM_SAFETY_ARGS = Object.freeze(["--use-mock-keychain", "--password-store=basic"]);
