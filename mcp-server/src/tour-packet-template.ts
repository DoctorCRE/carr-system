// Define the versioned client packet sections, facts and display values.
export const TOUR_PACKET_TEMPLATE_VERSION = "2.0.0";
export const TOUR_PACKET_TEMPLATE = Object.freeze({
  version: TOUR_PACKET_TEMPLATE_VERSION,
  brand: "CARR Healthcare Real Estate",
  map: "Tour map",
  title: "Your property tour",
  overview: "Tour day at a glance",
  properties: "Properties",
  notes: "My Notes",
  unknown: "Unknown",
  fields: [
    ["suite", "Suite"],
    ["property_type", "Property type"],
    ["size", "Size"],
    ["asking_economics", "Asking economics"],
    ["availability", "Availability"],
    ["parking", "Parking"],
  ] as const,
});
type Metric = {
  value?: string | number;
  min?: string | number;
  max?: string | number;
  currency?: string;
  unit?: string;
  period?: string;
};
export type PacketProperty = {
  property_ref: string;
  route_sequence: number;
  route_label: string;
  name: string;
  address: string;
  suite?: string;
  property_type?: string;
  size?: Metric;
  asking_economics?: Metric;
  availability?: string;
  parking?: string;
};
export function formatPacketMetric(value: Metric): string {
  const quantity =
    value.value !== undefined
      ? String(value.value)
      : [value.min, value.max].filter((v) => v !== undefined).join(" to ");
  return [value.currency, quantity, value.unit, value.period ? `per ${value.period}` : ""].filter(Boolean).join(" ");
}
export function tourPacketPresentation(properties: readonly PacketProperty[]) {
  const stops = [...properties]
    .sort((a, b) => a.route_sequence - b.route_sequence)
    .map((property) => ({
      property_ref: property.property_ref,
      route_sequence: property.route_sequence,
      route_label: property.route_label,
      name: property.name,
      address: property.address,
      facts: TOUR_PACKET_TEMPLATE.fields.map(([key, label]) => {
        const value = property[key];
        return {
          label,
          value:
            value == null
              ? TOUR_PACKET_TEMPLATE.unknown
              : typeof value === "object"
                ? formatPacketMetric(value)
                : String(value),
        };
      }),
    }));
  return {
    ...TOUR_PACKET_TEMPLATE,
    summary: `${stops.length} ${stops.length === 1 ? "property" : "properties"} in tour order.`,
    stops,
  };
}
