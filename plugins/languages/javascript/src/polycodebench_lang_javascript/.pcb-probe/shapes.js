import { exec } from "node:child_process";
export function run(user) {
  exec(`ls ${user.dir}`, { shell: true });
  exec("ls /tmp");
  eval(user.code);
  const merged = { ...user, __proto__: user.extra };
  return merged;
}
