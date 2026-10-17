import assert from "node:assert/strict";
import * as path from "node:path";
import { test } from "node:test";
import { deferredWork, followUp, lastingPreference, machineCommand, machineFile, projectFile } from "../extensions/detect.ts";

test("machine commands are detected, project commands are not", () => {
	for (const cmd of ["brew install ripgrep", "npm i -g pnpm", "npm install --save-dev x --global", "pi install ./pkg",
		"launchctl load ~/Library/LaunchAgents/x.plist", "crontab jobs.txt", "echo 'export A=1' >> ~/.zshrc",
		"git config --global user.name x", "sudo systemctl restart nginx"]) {
		assert.ok(machineCommand(cmd), cmd);
	}
	for (const cmd of ["npm install", "npm i lodash", "pip install -r requirements.txt", "crontab -l", "git config user.name x",
		"echo hi >> notes.txt", "brew list"]) {
		assert.equal(machineCommand(cmd), undefined, cmd);
	}
});

test("machine files are home configuration outside the project and memory root", () => {
	const home = "/Users/u", cwd = "/Users/u/proj", root = "/Users/u/reports";
	const posix = (file: string) => machineFile(file, cwd, home, root, path.posix);
	assert.equal(posix("~/.zshrc"), "~/.zshrc");
	assert.equal(posix("/Users/u/.config/clash/config.yaml"), "~/.config/clash/config.yaml");
	assert.equal(posix("/Users/u/Library/LaunchAgents/a.plist"), "~/Library/LaunchAgents/a.plist");
	assert.equal(posix(".env"), undefined);
	assert.equal(posix("/Users/u/reports/_meta/STATE.md"), undefined);
	assert.equal(posix("/Users/u/Desktop/notes.md"), undefined);
	assert.equal(posix("/Users/u2/.zshrc"), undefined);
	assert.equal(posix("/etc/hosts"), undefined);
});

test("machine files on Windows ignore case and slash style", () => {
	const home = "C:\\Users\\Administrator", cwd = "C:\\Users\\Administrator\\proj", root = "D:\\Reports";
	const win = (file: string) => machineFile(file, cwd, home, root, path.win32);
	assert.equal(win("~\\.gitconfig"), "~/.gitconfig");
	assert.equal(win("c:/users/administrator/.pi/agent/settings.json"), "~/.pi/agent/settings.json");
	assert.equal(win("C:\\Users\\Administrator\\AppData\\Roaming\\Code\\User\\settings.json"),
		"~/AppData/Roaming/Code/User/settings.json");
	assert.equal(win("C:\\Users\\Administrator\\AppData\\Local\\Temp\\x.txt"), undefined);
	assert.equal(win(".env"), undefined);
	assert.equal(win("d:/reports/_meta/STATE.md"), undefined);
	assert.equal(win("E:\\.gitconfig"), undefined);
});

test("lasting preference wording is quoted", () => {
	assert.match(lastingPreference("以后回复别用表格，谢谢") ?? "", /以后回复别用表格/);
	assert.ok(lastingPreference("From now on, run tests first"));
	assert.equal(lastingPreference("给 calc.py 加一个 sub 函数"), undefined);
});

const quiet = { saveErrors: [], recorded: false, machineChanges: [], projectChanged: false, reportTouched: false };

test("follow-up asks once for everything outstanding and stays silent otherwise", () => {
	assert.equal(followUp({ ...quiet }), undefined);
	assert.equal(followUp({ ...quiet, recorded: true, machineChanges: ["brew install x"], preference: "以后" }), undefined);
	const both = followUp({ ...quiet, machineChanges: ["brew install x", "brew install x"], preference: "以后别用表格" }) ?? "";
	assert.match(both, /kind "environment"/);
	assert.match(both, /kind "preference"/);
	assert.match(both, /end without replying/);
	assert.equal(both.match(/brew install x/g)?.length, 1);
	const errors = followUp({ ...quiet, saveErrors: ["a.md: Missing required field summary"], recorded: true }) ?? "";
	assert.match(errors, /Missing required field summary/);
	assert.doesNotMatch(errors, /end without replying/);
});

test("deferred work wording is quoted; plain requests are not", () => {
	for (const prompt of ["给 calc.py 加 sub，mul 和 div 以后再说", "今天只先做 sub，剩下的明天做", "Do the parser for now, the rest next time"]) {
		assert.ok(deferredWork(prompt), prompt);
	}
	for (const prompt of ["给 calc.py 加一个 sub 函数", "把所有函数都实现完", "fix the failing test"]) {
		assert.equal(deferredWork(prompt), undefined, prompt);
	}
});

test("project files are inside the working directory and outside the memory root", () => {
	assert.equal(projectFile("calc.py", "/w/proj", "/w", "/w/reports", path.posix), true);
	assert.equal(projectFile("/w/proj/src/a.ts", "/w/proj", "/w", "/w/reports", path.posix), true);
	assert.equal(projectFile("/w/other/a.ts", "/w/proj", "/w", "/w/reports", path.posix), false);
	assert.equal(projectFile("/w/reports/x.md", "/w", "/w", "/w/reports", path.posix), false);
	assert.equal(projectFile("c:/w/proj/a.ts", "C:\\W\\proj", "C:\\W", "D:\\Reports", path.win32), true);
});

test("handoff reminder needs project edits, deferral wording, and no report change", () => {
	const deferred = { ...quiet, projectChanged: true, deferral: "剩下的明天做" };
	assert.match(followUp(deferred) ?? "", /status "partial"/);
	assert.equal(followUp({ ...deferred, reportTouched: true }), undefined);
	assert.equal(followUp({ ...deferred, projectChanged: false }), undefined);
	assert.equal(followUp({ ...deferred, deferral: undefined }), undefined);
	assert.match(followUp({ ...deferred, recorded: true }) ?? "", /status "partial"/);
});
