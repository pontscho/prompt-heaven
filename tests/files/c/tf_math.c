/**
 * @file tf_math.c
 * @brief Fixture definitions matching tf_math.h.
 *
 * FIXTURE ONLY -- see tests/files/README.md.
 */
#include <math.h>
#include <stddef.h>

#include "tf_math.h"

tf_vec_t tf_vec_add(tf_vec_t a, tf_vec_t b)
{
	tf_vec_t out;

	out.tf_x = a.tf_x + b.tf_x;
	out.tf_y = a.tf_y + b.tf_y;
	out.tf_z = a.tf_z + b.tf_z;
	out.tf_unit = a.tf_unit;
	return out;
}

tf_vec_t tf_vec_scale(tf_vec_t v, double factor)
{
	tf_vec_t out;

	out.tf_x = v.tf_x * factor;
	out.tf_y = v.tf_y * factor;
	out.tf_z = v.tf_z * factor;
	out.tf_unit = v.tf_unit;
	return out;
}

double tf_vec_length(const tf_vec_t *v)
{
	if (v == NULL)
		return -1.0;

	return sqrt(v->tf_x * v->tf_x + v->tf_y * v->tf_y + v->tf_z * v->tf_z);
}
