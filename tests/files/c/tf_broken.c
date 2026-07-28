/**
 * @file tf_broken.c
 * @brief DELIBERATELY BROKEN fixture for the diagnostics path.
 *
 * FIXTURE ONLY -- see tests/files/README.md. **Do not fix this file.** A test
 * asserts that clangd (through purity_call) reports a problem here; repairing it
 * would silently disable that assertion. It is never part of a build.
 *
 * The planted defects:
 *   - tf_vec_length() takes a pointer, it is called with a value  -> type error
 *   - tf_undeclared_helper() is never declared                    -> unknown id
 */
#include "tf_math.h"

double tf_broken_entry(void)
{
	tf_vec_t v = { 1.0, 0.0, 0.0, TF_UNIT_METRE };

	/* passing the struct by value where a const tf_vec_t * is required */
	double len = tf_vec_length(v);

	return len + tf_undeclared_helper(3);
}
