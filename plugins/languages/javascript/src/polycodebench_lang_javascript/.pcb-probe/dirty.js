const apiKey = "sk-live-abcdef";

export async function load(user) {
  for (const id of [1, 2, 3]) {
    await fetchRecord(id);
  }
  loadAll([1, 2]);
  require('child_process').exec(`ls ${user.dir}`, { shell: true });
  if (user.count == "3") { return user.value; }
  if (user.value == null) { return 0; }
  document.body.innerHTML = user.name + "<b>x</b>";
  const parsed = JSON.parse(user.payload);
  return parsed;
}
