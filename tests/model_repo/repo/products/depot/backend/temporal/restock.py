from products.depot.backend.temporal.restock_helpers import batch_size


class RestockWorkflow:
    def run(self, item_id):
        return batch_size(item_id)
