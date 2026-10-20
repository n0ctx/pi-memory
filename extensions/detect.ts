import * as path from "node:path";

// Shell commands that change the machine rather than the project: installs, services, global config.
const MACHINE_COMMAND =
	/\b(?:brew\s+(?:install|uninstall|reinstall|upgrade|services\s+(?:start|stop|restart))|port\s+install|(?:npm\s+(?:i|install|uninstall|update|rm)|pnpm\s+(?:add|remove))\s+(?:\S+\s+)*?(?:-g|--global)\b|yarn\s+global\s+(?:add|remove)|pipx\s+install|uv\s+tool\s+install|cargo\s+install|go\s+install|pi\s+(?:install|remove|update)|launchctl\s+(?:load|unload|bootstrap|bootout|enable|disable|kickstart)|defaults\s+write|systemctl\s+(?:--user\s+)?(?:enable|disable|start|stop|restart)|apt(?:-get)?\s+(?:install|remove|purge)|winget\s+(?:install|uninstall)|git\s+config\s+--global|chsh)\b|\bcrontab\s+(?!-l\b)\S|(?:>>?|\btee(?:\s+-a)?)\s*(?:~|\$HOME)\/\./;

// Wording, in Chinese or English, that marks a lasting preference or correction rather than a one-off instruction.
const LASTING =
	/以后|今后|往后|从现在起|从今往后|别再|不要再|再也不|每次都|一律|记住|又忘|说过多少次|下次(?:别|不要|记得)|\bfrom now on\b|\bnever again\b|\bgoing forward\b|\bremember (?:that|to)\b|\bin (?:the )?future\b|\balways\b|\bstop (?:doing|using)\b/i;

// Wording, in Chinese or English, that leaves part of the work for a later session.
const DEFERRAL =
	/先(?:只)?做|只先做|剩下的|剩余的|其余的|其他的?(?:等|留|以后|下次|明天)|明天|下次|回头再|改天|以后再|晚点再|稍后再|之后再|留到|\b(?:later|tomorrow|next (?:time|session)|for now|the rest|remaining (?:work|parts|steps))\b/i;

export function machineCommand(command: string): string | undefined {
	return MACHINE_COMMAND.test(command) ? command.trim().split("\n")[0].slice(0, 80) : undefined;
}

function resolve(file: string, cwd: string, home: string, p: path.PlatformPath): string {
	return p.resolve(cwd, file.replace(/^~(?=$|[\\/])/, home));
}

// relative() also handles Windows drive letters and case-insensitive paths.
function within(abs: string, dir: string, p: path.PlatformPath): boolean {
	const rel = p.relative(p.resolve(dir), abs);
	return rel === "" || (!rel.startsWith("..") && !p.isAbsolute(rel));
}

/**
 * A file outside the project and memory root that holds machine configuration: home dotfiles,
 * macOS LaunchAgents and Preferences, Windows AppData/Roaming. `p` is the platform's path module.
 */
export function machineFile(
	file: string,
	cwd: string,
	home: string,
	memoryRoot: string,
	p: path.PlatformPath = path,
): string | undefined {
	const abs = resolve(file, cwd, home, p);
	const inside = (dir: string) => within(abs, dir, p);
	if (!inside(home) || inside(cwd) || inside(memoryRoot)) return undefined;
	const rel = p.relative(p.resolve(home), abs).split(p.sep).join("/");
	const config = rel.startsWith(".") || /^(?:Library\/(?:LaunchAgents|Preferences)|AppData\/Roaming)\//i.test(rel);
	return config ? `~/${rel}` : undefined;
}

function quote(rx: RegExp, prompt: string): string | undefined {
	const m = rx.exec(prompt);
	if (!m) return undefined;
	const start = Math.max(0, m.index - 30);
	return prompt.slice(start, m.index + 50).replace(/\s+/g, " ").trim();
}

/** The phrase around lasting-preference wording in a user prompt, if any. */
export function lastingPreference(prompt: string): string | undefined {
	return quote(LASTING, prompt);
}

/** The phrase around wording that defers part of the work to a later session, if any. */
export function deferredWork(prompt: string): string | undefined {
	return quote(DEFERRAL, prompt);
}

/** Hidden note for the start of a run whose prompt may need recording, or undefined. */
export function promptReminder(prompt: string): string | undefined {
	const parts: string[] = [];
	const preference = lastingPreference(prompt);
	if (preference) {
		parts.push(
			`pi-memory: the user's message may state a lasting preference or correction ("${preference}"). ` +
				'If it does, record it with the memory tool (action "note", kind "preference") in the user\'s words before your final reply.',
		);
	}
	const deferral = deferredWork(prompt);
	if (deferral) {
		parts.push(
			`pi-memory: the user's message leaves part of the work for later ("${deferral}"). ` +
				"If work remains when you finish, record a handoff with the memory tool before your final reply: " +
				'update the open report on the memory card if there is one, otherwise action "report" with status "partial" and the remaining items in open.',
		);
	}
	return parts.length ? parts.join("\n\n") : undefined;
}

/** Note appended to the result of a tool call that changed machine configuration. */
export function machineReminder(change: string): string {
	return (
		`pi-memory: this changed machine configuration (${change}). ` +
		'If the change outlasts this task, record it with the memory tool (action "note", kind "environment") before your final reply, giving old and new values.'
	);
}

/** Follow-up for a run whose memory changes could not be saved. */
export function saveErrorReminder(errors: string[]): string {
	return (
		"pi-memory could not save memory changes:\n" +
		errors.map((e) => `- ${e}`).join("\n") +
		"\nFix these files (edit them or use the memory tool); they are committed automatically once valid."
	);
}
