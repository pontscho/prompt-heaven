--- @file tf_broken.lua
--- @brief DELIBERATELY BROKEN fixture for the diagnostics path.
---
--- FIXTURE ONLY -- see tests/files/README.md. **Do not fix this file.** A test
--- asserts that lua-language-server (through purity_call) reports a problem here;
--- repairing it would silently disable that assertion.
---
--- The planted defects:
---   - a syntax error: the if block is never closed with `end`
---   - an undefined global is read

local function tfBrokenEntry(v)
	if v == nil then
		return tfUndefinedGlobal

	return v * 2
end

return tfBrokenEntry
