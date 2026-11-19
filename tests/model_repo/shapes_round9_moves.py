"""The rows of rounds 4 to 8 whose answer round 9 moves (`engine/brief-watched-round9.md`), each
with what it gives now (`outcome` and `reason`, or `unread` for the first shapes) and why (`moved`).
Every move but one is a read the round no longer makes: it fails closed. The pinned data stays as it
was; `moved` lays this over it. Round 9b (`moved9b`) moves the `__globals__` rows limit 9 kept to
unread, and marks `alone` the rows whose change reaches every module: they run in an answer of
their own."""

from __future__ import annotations

from typing import Any

MOVES: dict[str, dict[str, Any]] = {
    "ld_comprehension": {
        "moved": "round 9 B: a loader handed an item (T14) is dropped; at module level the "
        "list is unread, a problem as core registers it",
        "outcome": "problem",
        "reason": "ld_comprehension",
    },
    "ld_module_loop": {
        "moved": "round 9 B: a loader handed an item (T14) is dropped; at module level the list "
        "is unread, a problem as core registers it",
        "outcome": "problem",
        "reason": "ld_module_loop",
    },
    "lists_values": {"moved": "round 9 B: the builtin copy readers (T5) are dropped", "unread": True},
    "r4_d_slice_end": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r4_d_slice_end.backend.facade.temporal.WORKFLOWS",
    },
    "r4_e_body_rebind": {
        "moved": "round 9 C (t30's ruling (a)): a class attribute is read back only when the "
        "body binds it once; this body binds it twice",
        "outcome": "unread",
        "reason": "products.r4_e_body_rebind.backend.facade.temporal.Registration.WORKFLOWS",
    },
    "r5_g_import_module_self": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a computed module value "
        "counts in every module, so the row runs alone",
    },
    "r5_g_sysmod_spec": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a computed module value counts "
        "in every module, so the row runs alone",
    },
    "r5_g_tuple_holds_list": {
        "moved": "round 9 A: the holder's escape also lands on _BASE, the root list, which the answer names first",
        "outcome": "unread",
        "reason": "products.r5_g_tuple_holds_list.backend.facade.temporal._BASE",
    },
    "r5_h_class_body_modlist": {
        "moved": "round 9 B: a loader handed an item (T14) is dropped; in a class body "
        "the list is unread, a problem as core registers it",
        "outcome": "problem",
        "reason": "r5_h_class_body_modlist",
    },
    "r5_h_if_loader": {
        "moved": "round 9 B: a loader handed an item (T14) is dropped; in a module-level block "
        "the list is unread, a problem as core registers it",
        "outcome": "problem",
        "reason": "r5_h_if_loader",
    },
    "r5_o_assert_unique": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r5_o_assert_unique.backend.facade.temporal.WORKFLOWS",
    },
    "r5_o_lambda_read": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r5_o_lambda_read.backend.facade.temporal.WORKFLOWS",
    },
    "r5_o_other_class_same": {
        "moved": "round 9 A (T11 in a class body): an annotation does not shadow the "
        "module's name, so the body's WORKFLOWS.append may change the module's "
        "list",
        "outcome": "unread",
        "reason": "products.r5_o_other_class_same.backend.facade.temporal.WORKFLOWS",
    },
    "r6_g_builtins_patch_init": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6_g_builtins_patch_init.backend.facade.temporal.WORKFLOWS",
    },
    "r6_h_func_globals_env": {
        "moved9b": "round 9b (open point 1): a visible change through a function's "
        "`__globals__` is a change of its module's list, no longer limit 9; in "
        "a registration point the list is a problem of the answer",
        "outcome": "problem",
        "reason": "r6_h_func_globals_env",
    },
    "r6_n_func_globals": {
        "moved9b": "round 9b (open point 1): a visible change through a function's "
        "`__globals__` is a change of its module's list, no longer limit 9",
        "outcome": "unread",
        "reason": "products.r6_n_func_globals.backend.facade.temporal.WORKFLOWS",
    },
    "r6_n_lambda_globals": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a visible change through a function's "
        "`__globals__` is a change of its module's list, no longer limit 9; a "
        "lambda's `__globals__` names no module, so the row runs alone",
        "outcome": "unread",
        "reason": "products.r6_n_lambda_globals.backend.facade.temporal.WORKFLOWS",
    },
    "r6_o_dict_fromkeys": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6_o_dict_fromkeys.backend.facade.temporal.WORKFLOWS",
    },
    "r6_o_hash_tuple": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the builtin "
        "scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6_o_hash_tuple.backend.facade.temporal.WORKFLOWS",
    },
    "r6_o_isinstance": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6_o_isinstance.backend.facade.temporal.WORKFLOWS",
    },
    "r6_o_print": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6_o_print.backend.facade.temporal.WORKFLOWS",
    },
    "r6_o_sorted_key": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6_o_sorted_key.backend.facade.temporal.WORKFLOWS",
    },
    "r6_o_zip_dict": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r6_o_zip_dict.backend.facade.temporal.WORKFLOWS",
    },
    "r6_t_func_globals": {
        "moved9b": "round 9b (open point 1): a visible change through a function's "
        "`__globals__` is a change of its module's list, no longer limit 9",
        "outcome": "unread",
        "reason": "products.r6_t_func_globals.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_all": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_all.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_any": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_any.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_bool": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_bool.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_dict": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_dict.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_fromkeys": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_fromkeys.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_frozenset": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_frozenset.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_hash": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_hash.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_len": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_len.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_list": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_list.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_print": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_print.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_repr": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_repr.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_set": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_set.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_sorted": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_sorted.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_str": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_str.backend.facade.temporal.WORKFLOWS",
    },
    "r6r_tuple": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r6r_tuple.backend.facade.temporal.WORKFLOWS",
    },
    "r7_h_ok_modules_len": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped: len(MODULES) "
        "leaves the registration point's list unread, a problem",
        "outcome": "problem",
        "reason": "r7_h_ok_modules_len",
    },
    "r7_o_chain_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7_o_chain_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7_o_enumerate_comp": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7_o_enumerate_comp.backend.facade.temporal.WORKFLOWS",
    },
    "r7_o_enumerate_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7_o_enumerate_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7_o_map_lambda": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7_o_map_lambda.backend.facade.temporal.WORKFLOWS",
    },
    "r7_o_reversed_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7_o_reversed_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7_o_zip_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7_o_zip_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7_r_rec_property_decl": {
        "moved": "round 9 C: a decorated def in a record class's body (a property, a setter) makes it no record class",
        "outcome": "unread",
        "reason": "products.r7_r_rec_property_decl.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_dict": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_dict.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_fromkeys": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_fromkeys.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_frozenset": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_frozenset.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_list": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_list.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_set": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_set.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_sorted": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_sorted.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_sorted_key": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_sorted_key.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_sorted_key_builtin": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_sorted_key_builtin.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t5_tuple": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t5_tuple.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_all": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_all.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_any": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_any.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_bool": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_bool.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_hash": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the builtin "
        "scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_hash.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_isinstance": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_isinstance.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_len": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_len.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_max": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_max.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_min": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_min.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_print": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_print.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_repr": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_repr.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_str": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_str.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t6_sum": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t6_sum.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_chain_comp": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_chain_comp.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_enumerate_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_enumerate_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_filter_none": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_filter_none.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_iter_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_iter_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_map_lambda": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_map_lambda.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_reversed_for": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_reversed_for.backend.facade.temporal.WORKFLOWS",
    },
    "r7e_t8_zip_dict": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r7e_t8_zip_dict.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t13_radd_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t13_radd_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t13_radd_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t13_radd_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t13_radd_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t13_radd_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_fromkeys_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_fromkeys_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_fromkeys_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_fromkeys_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_fromkeys_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_fromkeys_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_list_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_list_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_list_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_list_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_list_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_list_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_set_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_set_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_set_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_set_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_set_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_set_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_tuple_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_tuple_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_tuple_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_tuple_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t5_tuple_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t5_tuple_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_isinstance_cls": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_isinstance_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_isinstance_clsg": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_isinstance_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_isinstance_mod": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_isinstance_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_len_cls": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_len_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_len_clsg": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_len_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_len_mod": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_len_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_repr_cls": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_repr_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_repr_clsg": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_repr_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t6_repr_mod": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t6_repr_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_chain_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_chain_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_chain_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_chain_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_chain_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_chain_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_enumerate_cls": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_enumerate_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_enumerate_clsg": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_enumerate_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_enumerate_mod": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_enumerate_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_filter_cls": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_filter_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_filter_clsg": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_filter_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_filter_mod": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_filter_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_map_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_map_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_map_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_map_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_map_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_map_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_zip_cls": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_zip_cls.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_zip_clsg": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_zip_clsg.backend.facade.temporal.WORKFLOWS",
    },
    "r8_m_t8_zip_mod": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_m_t8_zip_mod.backend.facade.temporal.WORKFLOWS",
    },
    "r8_n_t10_in_t8": {
        "moved": "round 9 B: the iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_n_t10_in_t8.backend.facade.temporal.WORKFLOWS",
    },
    "r8_n_t5_in_t5": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_n_t5_in_t5.backend.facade.temporal.WORKFLOWS",
    },
    "r8_n_t7_for_in_t7": {
        "moved": "round 9 A: an inline comprehension of items is a copy (one kind function), "
        "and iterating it reads it; the row never registers B",
        "outcome": "watched",
        "probes": ["backend/temporal/nt7forint7a.py"],
    },
    "r8_o_t5_fromkeys_val": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t5_fromkeys_val.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t5_sorted_keyfn": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t5_sorted_keyfn.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t5_sorted_keylam": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t5_sorted_keylam.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t5_sorted_rev": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t5_sorted_rev.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t6_print_other": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t6_print_other.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_chain_other": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_chain_other.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_enum_start": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_enum_start.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_filter_fn": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_filter_fn.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_map_fn": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the iterator "
        "builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_map_fn.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_map_other_iter": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_map_other_iter.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_zip_other": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_zip_other.backend.facade.temporal.WORKFLOWS",
    },
    "r8_o_t8_zip_strict": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_o_t8_zip_strict.backend.facade.temporal.WORKFLOWS",
    },
    "r8_p3_t13_name_eq_same": {
        "moved": "round 9 B: T13 reads no name operand",
        "outcome": "unread",
        "reason": "products.r8_p3_t13_name_eq_same.backend.facade.temporal.WORKFLOWS",
    },
    "r8_p3_t14_item_package": {
        "moved": "round 9 B: a loader handed an item (T14) is dropped",
        "outcome": "unread",
        "reason": "products.r8_p3_t14_item_package.backend.facade.temporal.WORKFLOWS",
    },
    "r8_p3_t8_decorated": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped; round 9 B: the "
        "iterator builtins (T8) are dropped",
        "outcome": "unread",
        "reason": "products.r8_p3_t8_decorated.backend.facade.temporal.WORKFLOWS",
    },
    "r8_p3_t8_key_holder_item": {
        "moved": "round 9 B: the builtin copy readers (T5) are dropped",
        "outcome": "unread",
        "reason": "products.r8_p3_t8_key_holder_item.backend.facade.temporal.WORKFLOWS",
    },
    "r8b_alias_other_module": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8b_alias_other_module.backend.facade.temporal.WORKFLOWS",
    },
    "r8b_relist_read": {
        "moved": "round 9 B: the builtin scalar readers (T6) are dropped",
        "outcome": "unread",
        "reason": "products.r8b_relist_read.backend.facade.temporal.WORKFLOWS",
    },
    "r9_g4_bound": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives counts "
        "in every module, so the row runs alone",
    },
    "r9_g4_bound_globals": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives "
        "counts in every module, so the row runs alone",
    },
    "r9_g4_class_method": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives "
        "counts in every module, so the row runs alone",
    },
    "r9_g4_copy_idx_globals": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name "
        "gives counts in every module, so the row runs alone",
    },
    "r9_g4_direct": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives counts "
        "in every module, so the row runs alone",
    },
    "r9_g4_for": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives counts in "
        "every module, so the row runs alone",
    },
    "r9_g4_get": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives counts in "
        "every module, so the row runs alone",
    },
    "r9_g4_max_globals": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives "
        "counts in every module, so the row runs alone",
    },
    "r9_g4_store_globals": {
        "alone": True,
        "moved9b": "round 9b (open point 1): a change through a `__globals__` no name gives "
        "counts in every module, so the row runs alone",
    },
}


def moved(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The rows with round 9's moves laid over them."""
    return {p: {**row, **MOVES[p]} if p in MOVES else row for p, row in rows.items()}
