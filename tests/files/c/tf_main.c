/**
 * @file tf_main.c
 * @brief Fixture call sites, so find_references has more than one hit.
 *
 * FIXTURE ONLY -- see tests/files/README.md. tf_vec_add is referenced twice
 * here, tf_vec_scale once and tf_vec_length once; together with the declaration
 * in tf_math.h and the definition in tf_math.c that gives a reference count a
 * test can assert on.
 */
#include <stdio.h>

#include "tf_math.h"

int main(void)
{
	tf_vec_t a = { 1.0, 2.0, 2.0, TF_UNIT_METRE };
	tf_vec_t b = { 0.5, 0.5, 0.5, TF_UNIT_METRE };
	tf_vec_t sum = tf_vec_add(a, b);
	tf_vec_t twice = tf_vec_add(sum, sum);
	tf_vec_t half = tf_vec_scale(twice, 0.5);

	printf("dim=%d length=%f\n", TF_VEC_DIM, tf_vec_length(&half));
	return 0;
}
