/**
 * @file tf_math.h
 * @brief Fixture declarations for exercising compiler-accurate navigation.
 *
 * FIXTURE ONLY -- never compiled into the project. Every name is tf-prefixed so
 * a repo-wide search for a real symbol cannot match this file. See
 * tests/files/README.md.
 */
#ifndef TF_MATH_H
#define TF_MATH_H

/** Number of components in a tf_vec_t. */
#define TF_VEC_DIM 3

/** Unit a vector is expressed in. */
typedef enum tf_unit {
	TF_UNIT_METRE = 0,
	TF_UNIT_INCH  = 1
} tf_unit_t;

/** A three-component vector plus the unit it is measured in. */
typedef struct tf_vec {
	double    tf_x;
	double    tf_y;
	double    tf_z;
	tf_unit_t tf_unit;
} tf_vec_t;

/**
 * @brief Component-wise sum. Both operands must share a unit.
 * @param a First operand.
 * @param b Second operand.
 * @return The sum, in the unit of @p a.
 */
tf_vec_t tf_vec_add(tf_vec_t a, tf_vec_t b);

/**
 * @brief Scale every component.
 * @param v Vector to scale.
 * @param factor Multiplier.
 * @return The scaled vector.
 */
tf_vec_t tf_vec_scale(tf_vec_t v, double factor);

/**
 * @brief Euclidean length.
 * @param v Vector to measure; must not be NULL.
 * @return The length, or -1.0 when @p v is NULL.
 */
double tf_vec_length(const tf_vec_t *v);

#endif /* TF_MATH_H */
