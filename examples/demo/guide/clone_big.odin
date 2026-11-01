// Native Odin code keeps shallow header assignments. Clone explicitly when
// returning an independent owner to a Thor caller.
package guide

import "thor:big"

clone_big :: proc(value: big.Integer) -> big.Integer {
    return big.clone(value)
}
