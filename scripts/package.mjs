// Builds out/HelixSR-Manager.zip in the layout Decky's "Install plugin from ZIP" expects:
// one top folder with plugin.json, package.json, main.py, dist/index.js, LICENSE, README.md.
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const name = "HelixSR-Manager";
rmSync("out", { recursive: true, force: true });
mkdirSync(`out/${name}/dist`, { recursive: true });
for (const f of ["plugin.json", "package.json", "main.py", "LICENSE", "README.md"]) cpSync(f, `out/${name}/${f}`);
cpSync("dist/index.js", `out/${name}/dist/index.js`);
execFileSync("zip", ["-qr", `${name}.zip`, name], { cwd: "out", stdio: "inherit" });
console.log(`out/${name}.zip`);
