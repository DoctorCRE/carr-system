export function briefPreferences(storage, sponsor) {
  const key = `doctorcre:morning:${sponsor}`;
  let value = {};
  const read = () => { try { value = JSON.parse(storage?.getItem(key) || '{}') || {}; } catch {} return value; };
  read();
  const save = patch => { value = { ...read(), ...patch }; try { storage?.setItem(key,JSON.stringify(value)); } catch {} };
  return { get value() { return value; }, save };
}
