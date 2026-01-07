---
name: p:lua-guidelines
description: Lua coding guidelines including code style (tab indentation, camelCase naming, early returns), Doxygen-style documentation with @ parameters and -- comments, table utility functions (isempty, deepcopy, merge), and module template patterns. Use when writing or editing Lua files (.lua), implementing Lua modules, working with tables, or documenting Lua functions.
---

### 0. Code Implementation Guidelines

Follow these rules when you write code:

- Use early returns whenever possible to make the code more readable
- When ID is part of a variable the expected naming is 'Id' (e.g. motivationId)
- Use TAB for indentation, DON'T USE SPACE for it
- Keep code style of the source code in current file

### 1. Code Documentation Guidelines
- Use the following format for documenting classes and functions: Doxygen style
- Always use @ for doxygen parameters
- For function documentation use -- for single line comments
- For structure the source code to blocks use ---

### 2. Performance & Efficiency
- Algorithm complexity analysis
- Avoid unnecessary computation, copies, heap allocations and temporary objects
- Loop optimizations

### 3. Code Style & Standards
- TAB indentation (NO spaces)
- Consistent naming conventions ('Id' suffix for IDs)
- Early returns usage
- DRY principle adherence
- File structure and organization

## 5. Coding guidelines
- Follow the source code style of the current file
- Use cammelCase for variable names and function names
- Ensure proper use of `nil` checks
- Variable naming conventions (cammelCase)
- Comment begins with '--' for single line comments
- Valid global variable names:
    * poluah- main poluahmodule
- For managing tables have utility functions:
	* table.isempty(t) - checks if table is empty
	* table.isarray(t) - checks if table is an array
	* table.nkeys(t) - returns number of keys in table
	* table.deepcopy(t) - deep copy of a table
	* table.merge(t1, t2) - merges two tables (t2 into t1)
	* table.mergeTables(t1, t2) - merges two tables (t2 into t1) recursively
	* table.isContainsAsValue(t, value) - checks if table contains a value
	* table.isContainsAsKey(t, key) - checks if table contains a key
	* table.getNestedValue(t, key) - retrieves a nested value from a table
	* table.containsTable(t, subTable) - checks if a table contains another table
- Use `polua.log.<level>(fmt, ...)` for logging (where level is one of: emerg, alert, crit, err, warning, notice, info, debug)

## 6. Module Template
```lua
---
-- @file module_name.lua
-- @brief Brief description of the module
--

local m1, m2 =
	require "m1",
	require "m2"

-- local variable definitions
local variable = {}

---
-- Function declarations
--
-- @param config Configuration string or structure
--
-- @return Pointer to the created module instance, or NULL on error
--
local function method2(config)
	-- Implementation of the function
	return nil
end

return {
	create = function(config)
		-- other local variable definitions and initialization steps
		return {
			config = config,

			method = function()
				-- Implementation of the method
			end,

			method2 = method2
		}
	end
}

```
