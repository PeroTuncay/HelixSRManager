// Builds out/HelixSR-Manager.zip in the layout Decky's "Install plugin from ZIP" expects:
// one top folder with plugin.json, package.json, main.py, dist/index.js, LICENSE, README.md.
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const name = "HelixSR-Manager";

// Decky runs main.py on its own bundled Python, older than 3.12: syntax that only newer Pythons accept (e.g. the
// same quotes nested inside an f-string) makes the backend fail to load and the panel hang. Compile it with an older
// interpreter if one is around.
const oldPythons = ["/usr/bin/python3", "python3.11", "python3.10", "python3.9"];
let checked = false;
for (const py of oldPythons) {
  let version;
  try {
    version = execFileSync(py, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"], { encoding: "utf8" }).trim();
  } catch {
    continue;
  }
  const [major, minor] = version.split(".").map(Number);
  if (major !== 3 || minor >= 12) continue;
  try {
    execFileSync(py, ["-c", "import ast, sys; ast.parse(open('main.py').read())"], { stdio: "pipe" });
  } catch (e) {
    console.error(`main.py doesn't compile on Python ${version} (Decky's Python is older than 3.12):`);
    console.error(String(e.stderr));
    process.exit(1);
  }
  console.log(`main.py compiles on Python ${version}`);
  checked = true;
  break;
}
if (!checked) console.warn("warning: no Python older than 3.12 found, main.py's compatibility with Decky not checked");
rmSync("out", { recursive: true, force: true });
mkdirSync(`out/${name}/dist`, { recursive: true });
for (const f of ["plugin.json", "package.json", "main.py", "LICENSE", "README.md"]) cpSync(f, `out/${name}/${f}`);
cpSync("dist/index.js", `out/${name}/dist/index.js`);
execFileSync("zip", ["-qr", `${name}.zip`, name], { cwd: "out", stdio: "inherit" });
console.log(`out/${name}.zip`);
