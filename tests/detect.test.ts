import assert from "node:assert/strict";
import * as path from "node:path";
import { test } from "node:test";
import { deferredWork, lastingPreference, machineCommand, machineFile, machineReminder, promptReminder, saveErrorReminder } from "../extensions/detect.ts";

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

test("deferred work wording is quoted; plain requests are not", () => {
	for (const prompt of ["给 calc.py 加 sub，mul 和 div 以后再说", "今天只先做 sub，剩下的明天做", "Do the parser for now, the rest next time"]) {
		assert.ok(deferredWork(prompt), prompt);
	}
	for (const prompt of ["给 calc.py 加一个 sub 函数", "把所有函数都实现完", "fix the failing test"]) {
		assert.equal(deferredWork(prompt), undefined, prompt);
	}
});

test("prompt reminder covers preference and deferral wording and stays silent otherwise", () => {
	assert.equal(promptReminder("给 calc.py 加一个 sub 函数"), undefined);
	assert.match(promptReminder("以后回复别用表格") ?? "", /kind "preference"/);
	const deferred = promptReminder("今天只先做 sub，剩下的明天做") ?? "";
	assert.match(deferred, /status "partial"/);
	assert.doesNotMatch(deferred, /kind "preference"/);
	const both = promptReminder("以后都先跑测试，剩下的明天做") ?? "";
	assert.match(both, /kind "preference"/);
	assert.match(both, /status "partial"/);
});

test("machine and save-error reminders quote what happened", () => {
	assert.match(machineReminder("brew install x"), /\(brew install x\).*kind "environment"/);
	assert.match(saveErrorReminder(["a.md: Missing required field summary"]), /- a\.md: Missing required field summary/);
});
