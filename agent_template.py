"""Team-created minimal public-API example, not the organizer's template."""


class Agent:
    def act(self, env):
        profile = env.customer_profile
        tariff_ids = env.tariffs["tariff_id"].tolist()
        channel = min(env.channels, key=lambda name: env.channels[name]["cost"])
        for (arpu, data, current, call), frame in profile.groupby(
            ["arpu_segment", "data_segment", "current_tariff", "call_segment"], sort=True
        ):
            if 10 <= len(frame) <= 5000:
                filters = {"filter_arpu_segment": arpu, "filter_data_segment": data,
                           "filter_current_tariff": current, "filter_call_segment": call}
                choices = []
                for target in [t for t in tariff_ids if t != current][:2]:
                    result = env.run_pilot(target_tariff=target, channel=channel,
                                           n_customers=min(30, len(frame)), **filters)
                    choices.append((result["mean_arpu_uplift"], target))
                if choices:
                    _, target = max(choices)
                    return [{"campaign_name": "Template pilot winner", **filters,
                             "target_tariff": target, "channel": channel}]
        raise ValueError("No suitable demonstration cohort")
