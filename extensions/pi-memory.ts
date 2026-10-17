import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { StringEnum, Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { deferredWork, followUp, lastingPreference, machineCommand, machineFile, projectFile } from "./detect.ts";

const SCRIPTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "scripts");
const PYTHON = process.platform === "win32" ? ["py", "-3"] : ["python3"];
const HOME = os.homedir();
const ROOT = path.resolve((process.env.MEMQ_ROOT || "~/reports").replace(/^~(?=$|[\\/])/, HOME));

interface Result {
	code: number;
	out: string;
}

function run(cmd: string, args: string[], signal?: AbortSignal): Promise<Result> {
	return new Promise((resolve) => {
		// Python writes pipes in the locale code page on Windows unless told otherwise; output is decoded as UTF-8.
		const env = { ...process.env, PYTHONIOENCODING: "utf-8" };
		execFile(cmd, args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, signal, env }, (error, stdout, stderr) => {
			const code = error ? (typeof error.code === "number" ? error.code : 1) : 0;
			resolve({ code, out: stdout || stderr || (error ? error.message : "") });
		});
	});
}

function python(script: string, args: string[], signal?: AbortSignal): Promise<Result> {
	return run(PYTHON[0], [...PYTHON.slice(1), path.join(SCRIPTS, script), "--root", ROOT, ...args], signal);
}

async function projectOf(cwd: string): Promise<string> {
	const top = await run("git", ["-C", cwd, "rev-parse", "--show-toplevel"]);
	if (top.code === 0 && top.out.trim()) return path.basename(top.out.trim());
	return path.resolve(cwd) === HOME ? "" : path.basename(cwd);
}

export default function piMemory(pi: ExtensionAPI) {
	// Subagents report to the main agent, which owns memory; they get neither the card nor the tool.
	if ((process.env.PI_SUBAGENT_DEPTH ?? "0") !== "0") return;

	let project = "";
	let prompt = "";
	let recorded = false;
	let continued = false;
	let machineChanges: string[] = [];
	let projectChanged = false;
	let reportTouched = false;
	const resetRun = () => {
		prompt = "";
		recorded = false;
		continued = false;
		machineChanges = [];
		projectChanged = false;
		reportTouched = false;
	};

	pi.on("session_start", async (_event, ctx) => {
		if (fs.existsSync(path.join(ROOT, "_meta", "STATE.md"))) return;
		const init = await python("init-memory.py", []);
		if (init.code !== 0 && ctx.hasUI) ctx.ui.notify(`pi-memory: could not initialize ${ROOT}: ${init.out}`, "warning");
	});

	pi.on("before_agent_start", async (event, ctx) => {
		project = await projectOf(ctx.cwd);
		if (!continued) prompt = event.prompt;
		const card = await python("memcard.py", ["--project", project]);
		event.systemPromptOptions.sections.pi_memory = [
			`Cross-session memory (pi-memory) at ${ROOT}; current project: ${project || "none"}.`,
			"Use the memory tool to search it or record durable facts; changes are committed automatically.",
			"",
			card.code === 0 ? card.out.trim() : `(memory card unavailable: ${card.out.trim()})`,
		].join("\n");
	});

	pi.on("tool_call", (event, ctx) => {
		const input = event.input as { command?: string; path?: string };
		const change =
			event.toolName === "bash" && input.command
				? machineCommand(input.command)
				: (event.toolName === "edit" || event.toolName === "write") && input.path
					? machineFile(input.path, ctx.cwd, HOME, ROOT)
					: undefined;
		if (change) machineChanges.push(change);
		if ((event.toolName === "edit" || event.toolName === "write") && input.path) {
			projectChanged ||= projectFile(input.path, ctx.cwd, HOME, ROOT);
		}
	});

	pi.on("agent_before_settle", async (event, ctx) => {
		const saved = await python("memsave.py", ["--project", project]);
		let result = { changed: [] as string[], errors: [] as string[] };
		try {
			result = JSON.parse(saved.out);
		} catch {
			result.errors = [saved.out.trim() || `memsave exited with ${saved.code}`];
		}
		recorded ||= result.changed.length > 0;
		// Reports are the .md files at the top of the root; INDEX.md is generated.
		reportTouched ||= result.changed.some((f) => !f.includes("/") && f.endsWith(".md") && f !== "INDEX.md");
		const message = followUp({
			saveErrors: result.errors,
			recorded,
			machineChanges,
			preference: lastingPreference(prompt),
			projectChanged,
			reportTouched,
			deferral: deferredWork(prompt),
		});
		// Pi validates the continuation after the entry is appended; event.context predates it.
		if (message && !continued && event.outcome === "completed") {
			continued = true;
			return {
				entries: [{ type: "custom_message", customType: "pi-memory", content: message, display: false }],
				continue: true,
			};
		}
		if (result.errors.length && ctx.hasUI) {
			ctx.ui.notify(`pi-memory: memory changes not saved — ${result.errors[0]}`, "warning");
		}
	});

	pi.on("agent_settled", resetRun);

	pi.registerTool({
		name: "memory",
		label: "Memory",
		description: [
			"Cross-session memory shared by every Pi session on this machine. Stored as Markdown and committed automatically; never store secrets.",
			"- search: find past decisions, handoffs, machine facts, and preferences (query: keywords in Chinese and English, error codes, ports, component names).",
			"- note: one-line fact. kind=environment for machine configuration (installed tools, ports, services, models, config paths) — record changes in the same turn, with old and new values; kind=preference for a lasting user preference or correction, in the user's words; kind=global for a cross-project reminder (prefix [due YYYY-MM-DD] for a deadline). replaces: part of an existing line to rewrite it, or with empty text to delete it.",
			"- report: durable task record — a decision and its rationale, or unfinished work to hand off (status open or partial, open items, optional due, related: other report filenames). Filename, dates, and project are filled in. Refused if an unclosed report of this project has the same title; update that one instead.",
			"- update: change a report (file, status, summary, open, due; related adds links) and log what changed (change). Close handoffs when the work is done.",
		].join("\n"),
		promptSnippet: "Search or record cross-session memory: decisions, handoffs, machine facts, user preferences",
		promptGuidelines: [
			"Search memory when continuing earlier work, relying on a past decision or machine fact, or when the user refers to history; the memory card in the system prompt already lists open work and preferences.",
			"Record only durable information: decisions with rationale, unfinished-work handoffs, machine configuration changes, lasting user preferences. Ordinary completed work needs no record.",
		],
		parameters: Type.Object({
			action: StringEnum(["search", "note", "report", "update"] as const),
			query: Type.Optional(Type.String()),
			kind: Type.Optional(StringEnum(["environment", "preference", "global"] as const)),
			text: Type.Optional(Type.String()),
			replaces: Type.Optional(Type.String()),
			title: Type.Optional(Type.String()),
			summary: Type.Optional(Type.String({ description: "Conclusion only, at most 80 characters" })),
			status: Type.Optional(StringEnum(["open", "partial", "plan", "done", "abandoned", "superseded"] as const)),
			type: Type.Optional(StringEnum(["fix", "feature", "audit", "design", "ops", "handoff", "research"] as const)),
			tags: Type.Optional(Type.Array(Type.String(), { description: "5–15 search terms" })),
			open: Type.Optional(Type.Array(Type.String(), { description: "Open items; empty when done" })),
			due: Type.Optional(Type.String({ description: "YYYY-MM-DD" })),
			body: Type.Optional(Type.String({ description: "Report body in Markdown" })),
			related: Type.Optional(Type.Array(Type.String(), { description: "Filenames of related reports" })),
			file: Type.Optional(Type.String({ description: "Report filename" })),
			change: Type.Optional(Type.String()),
		}),
		async execute(_toolCallId, params, signal) {
			const { action } = params;
			let res: Result;
			if (action === "search") {
				const words = (params.query ?? "").split(/\s+/).filter(Boolean);
				if (!words.length) throw new Error("query is required for search");
				res = await python("memq.py", ["--project", project, ...words], signal);
			} else {
				const { action: _, ...data } = params;
				res = await python("memwrite.py", [action, "--project", project, "--json", JSON.stringify(data)], signal);
				if (res.code === 0) recorded = true;
			}
			if (res.code !== 0) throw new Error(res.out.trim());
			const text = res.out.length > 8000 ? `${res.out.slice(0, 8000)}\n… (truncated; refine the query)` : res.out;
			return { content: [{ type: "text", text }], details: undefined };
		},
	});
}
