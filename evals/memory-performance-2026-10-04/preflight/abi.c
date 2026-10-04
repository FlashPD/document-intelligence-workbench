#include <sys/resource.h>
#include <stddef.h>
#include <stdio.h>
int main(void) { printf("%zu %zu %zu\n", sizeof(struct rusage_info_v4), offsetof(struct rusage_info_v4, ri_phys_footprint), offsetof(struct rusage_info_v4, ri_lifetime_max_phys_footprint)); return 0; }
