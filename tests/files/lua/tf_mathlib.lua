--- @file tf_mathlib.lua
--- @brief Fixture module for exercising type-aware Lua navigation.
---
--- FIXTURE ONLY -- never required by the project. Every name is tf-prefixed so a
--- repo-wide search for a real symbol cannot match this file. See
--- tests/files/README.md.

local tfMathlib = {}

--- Number of components in a tfVec.
tfMathlib.tfVecDim = 3

--- @param x number
--- @param y number
--- @param z number
--- @return table tfVec a new vector
function tfMathlib.tfNewVec(x, y, z)
	return { tfX = x, tfY = y, tfZ = z }
end

--- @param a table first operand
--- @param b table second operand
--- @return table tfVec component-wise sum
function tfMathlib.tfAdd(a, b)
	return tfMathlib.tfNewVec(a.tfX + b.tfX, a.tfY + b.tfY, a.tfZ + b.tfZ)
end

--- @param v table vector to scale
--- @param factor number multiplier
--- @return table tfVec the scaled vector
function tfMathlib.tfScale(v, factor)
	return tfMathlib.tfNewVec(v.tfX * factor, v.tfY * factor, v.tfZ * factor)
end

--- @param v table vector to measure
--- @return number length euclidean length
function tfMathlib.tfLength(v)
	return math.sqrt(v.tfX * v.tfX + v.tfY * v.tfY + v.tfZ * v.tfZ)
end

return tfMathlib
