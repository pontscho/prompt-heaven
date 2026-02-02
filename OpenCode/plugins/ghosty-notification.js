// Ghostty Notification Plugin for OpenCode
// This plugin sends desktop notifications using Ghostty's OSC 777 escape sequence
// when specific events occur

export const GhosttyNotificationPlugin = async ({ client }) => {
	
	// Function to send desktop notification using notify-send (Linux)
	const sendNotification = async (title, message) => {
		try {
			const { exec } = require('child_process');
			
			// Use notify-send for Linux desktop notifications
			exec(`notify-send "${title.replace(/"/g, '\\"')}" "${message.replace(/"/g, '\\"')}"`, (error) => {
				if (error) {
					// Fallback to Ghostty OSC 777 if notify-send fails
					const notificationSequence = `\033]777;notify;${title};${message}\033\\`;
					process.stderr.write(notificationSequence);
				}
			});
			
			// Log to client
			await client.app.log({
				service: "ghostty-notification-plugin",
				level: "info",
				message: "Notification sent",
				extra: { title, message },
			});
			
			return true;
		} catch (error) {
			await client.app.log({
				service: "ghostty-notification-plugin",
				level: "error",
				message: "Failed to send notification",
				extra: { error: error.message },
			});
			return false;
		}
	};
	
	return {
		// Use the 'event' hook to listen to all events
		event: async ({ event }) => {
			const eventType = event.type;
			
			switch (eventType) {
				case "session.error": {
					await sendNotification("OpenCode Session Error", "An error occurred in the OpenCode session");
					break;
				}
				
				case "permission.asked": {
					// The permission data is in the event properties
					const permission = event.properties?.permission;
					if (permission) {
						await sendNotification("OpenCode Permission Request", `Permission requested: ${permission.type}`);
					}
					break;
				}
				
				case "session.idle": {
					// Session completed - could notify here if needed
					await sendNotification("OpenCode", "Session completed");
					break;
				}
			}
		},
		
		// Tool execution hooks are separate from event hooks
		"tool.execute.after": async (input) => {
			if (input.error) {
				await sendNotification("OpenCode Tool Failure", `Tool execution failed: ${input.error.message || input.error}`);
			}
			
			// Check for file edits
			if (input.tool === "edit" || input.tool === "write") {
				// Note: The edit tool output structure may vary
				// We'll count changes if available
				const changes = input.result?.changes || [];
				if (changes.length > 5) {
					await sendNotification("OpenCode File Changes", `Significant file changes detected: ${changes.length} changes`);
				}
			}
		},
	};
};
