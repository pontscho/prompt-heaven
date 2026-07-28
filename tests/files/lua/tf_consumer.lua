--- @file tf_consumer.lua
--- @brief Fixture call sites for the Lua fixtures.
---
--- FIXTURE ONLY -- see tests/files/README.md. tfAdd is referenced twice here so
--- a reference count has something to add up.

local tfMathlib = require("tf_mathlib")

local function tfRun()
	local a = tfMathlib.tfNewVec(1.0, 2.0, 2.0)
	local b = tfMathlib.tfNewVec(0.5, 0.5, 0.5)
	local sum = tfMathlib.tfAdd(a, b)
	local twice = tfMathlib.tfAdd(sum, sum)
	local half = tfMathlib.tfScale(twice, 0.5)

	return tfMathlib.tfLength(half), tfMathlib.tfVecDim
end

return { tfRun = tfRun }
