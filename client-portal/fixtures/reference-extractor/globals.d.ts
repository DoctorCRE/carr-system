// Synthetic source literals are parsed as JSON values, never executed.
type ReferenceLiteral = string | number | boolean | null | ReferenceLiteral[] | { [key: string]: ReferenceLiteral };

interface Window {
  referenceData: { [key: string]: ReferenceLiteral };
}
