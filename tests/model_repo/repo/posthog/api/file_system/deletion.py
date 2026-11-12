POST_DELETE_HOOKS = []


def register_post_delete_hook(hook):
    POST_DELETE_HOOKS.append(hook)
