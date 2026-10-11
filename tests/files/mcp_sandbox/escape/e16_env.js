// escape probe E16 (mcp_sandbox), node: the script's environment.
// Prints ONE JSON line: the sorted environment KEYS -- never a value.
console.log(JSON.stringify({keys: Object.keys(process.env).sort()}));
