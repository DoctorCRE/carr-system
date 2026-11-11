// Define the versioned client packet sections, facts and display values.
export const TOUR_PACKET_CONTRACT_VERSION = "2.1.0";
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
  appointment_start?: string | null;
  appointment_end?: string | null;
  public_packet_note?: string | null;
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
const appointmentFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago",
  weekday: "short",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZoneName: "short",
});
export function formatPacketAppointment(
  property: Pick<PacketProperty, "appointment_start" | "appointment_end">,
): string | null {
  if (!property.appointment_start) return null;
  const parts = (timestamp: string) => {
    const values = appointmentFormat.formatToParts(new Date(timestamp));
    const value = (type: Intl.DateTimeFormatPartTypes) => values.find((part) => part.type === type)?.value ?? "";
    return {
      date: `${value("weekday")}, ${value("month")} ${value("day")}`,
      year: value("year"),
      time: `${value("hour")}:${value("minute")}`,
      period: value("dayPeriod"),
      zone: value("timeZoneName"),
    };
  };
  const start = parts(property.appointment_start);
  const startTime = `${start.time} ${start.period} ${start.zone}`;
  if (!property.appointment_end) return `${start.date}, ${startTime}`;
  const end = parts(property.appointment_end);
  const endTime = `${end.time} ${end.period} ${end.zone}`;
  if (start.date !== end.date || start.year !== end.year) {
    const startDate = start.year === end.year ? start.date : `${start.date}, ${start.year}`;
    const endDate = start.year === end.year ? end.date : `${end.date}, ${end.year}`;
    return `${startDate}, ${startTime} to ${endDate}, ${endTime}`;
  }
  if (start.zone !== end.zone) return `${start.date}, ${startTime} to ${endTime}`;
  const from = start.period === end.period ? start.time : `${start.time} ${start.period}`;
  return `${start.date}, ${from} to ${endTime}`;
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
      appointment: formatPacketAppointment(property),
      public_packet_note: property.public_packet_note ?? null,
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
    contractVersion: TOUR_PACKET_CONTRACT_VERSION,
    summary: `${stops.length} ${stops.length === 1 ? "property" : "properties"} in tour order.`,
    stops,
  };
}
