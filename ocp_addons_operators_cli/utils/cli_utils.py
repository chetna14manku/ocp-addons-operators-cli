import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from ocp_utilities.must_gather import run_must_gather
from simple_logger.logger import get_logger

from ocp_addons_operators_cli.constants import SUPPORTED_ACTIONS
from ocp_addons_operators_cli.utils.addons_utils import (
    assert_addons_user_input,
    prepare_addons_action,
)
from ocp_addons_operators_cli.utils.general import set_debug_os_flags
from ocp_addons_operators_cli.utils.operators_utils import (
    assert_operators_user_input,
    prepare_operators_action,
)

LOGGER = get_logger(name=__name__)


def abort_no_ocm_token(ocm_token, addons):
    LOGGER.info("Verify OCM TOKEN is not missing from user input")
    if addons and not ocm_token:
        LOGGER.error("`--ocm-token` is required for addon installation")
        raise click.Abort()


def assert_action(action):
    LOGGER.info("Verify `action` from user input")
    if not action:
        LOGGER.error(
            f"'action' must be provided, supported actions: `{SUPPORTED_ACTIONS}`"
        )
        raise click.Abort()

    if action not in SUPPORTED_ACTIONS:
        LOGGER.error(
            "Provided 'action' is not supported, supported actions:"
            f" `{SUPPORTED_ACTIONS}`"
        )
        raise click.Abort()


def verify_user_input(**kwargs):
    action = kwargs.get("action")
    operators = kwargs.get("operators")
    addons = kwargs.get("addons")
    ocm_token = kwargs.get("ocm_token")
    brew_token = kwargs.get("brew_token")

    abort_no_ocm_token(ocm_token=ocm_token, addons=addons)

    assert_action(action=action)

    if not (operators or addons):
        LOGGER.error("At least one '--operator' or `--addon` option must be provided.")
        raise click.Abort()

    assert_operators_user_input(operators=operators, brew_token=brew_token)
    assert_addons_user_input(addons=addons, brew_token=brew_token)


def run_install_or_uninstall_products(
    operators, addons, parallel, debug, install, must_gather_output_dir
):
    if debug:
        set_debug_os_flags()

    futures = []
    action = "install" if install else "uninstall"

    with ThreadPoolExecutor() as executor:
        operators_action_list = prepare_operators_action(
            operators=operators,
            install=install,
        )

        addons_action_list = prepare_addons_action(
            addons=addons,
            install=install,
        )

        LOGGER.info(f"Running products installation; parallel: {parallel}")
        for product_action_tuple in addons_action_list + operators_action_list:
            action_func = product_action_tuple[0]
            action_kwargs = product_action_tuple[1]
            product_dict = product_action_tuple[2]
            if parallel:
                futures.append(executor.submit(action_func, **action_kwargs))
            else:
                try:
                    action_func(**action_kwargs)
                except Exception as ex:
                    LOGGER.error(
                        f"Failed to {action}: \n{ex}",
                    )
                    collect_must_gather(
                        must_gather_output_dir=must_gather_output_dir,
                        product=product_dict,
                    )
                    raise click.Abort()

            LOGGER.info(f"FUTURES:: {futures}")
            if futures:
                LOGGER.info(f"PRODUCT_DICT: {product_dict}")
                for result in as_completed(futures):
                    LOGGER.info(f"RESULT: {result}")
                    if result.exception():
                        # TODO: Add cluster name, product name and type to threads
                        LOGGER.error(
                            f"Failed to {action}: {result.exception()}\n",
                        )
                        collect_must_gather(
                            must_gather_output_dir=must_gather_output_dir,
                            product=product_dict,
                        )
                        raise click.Abort()

    addon_names = [addon["name"] for addon in addons]
    operator_names = [operator["name"] for operator in operators]
    LOGGER.info(
        f"Successfully {action} {f'addons: {addon_names}' if addons else ''} "
        f"{f'operators: {operator_names}' if operators else ''}"
    )


def set_parallel(user_input_parallel, operators, addons):
    LOGGER.info("Setting `parallel` option")
    if len(operators + addons) > 1:
        return user_input_parallel

    return False


def collect_must_gather(must_gather_output_dir, product):
    cluster_name = product["cluster_name"]
    kubeconfig_path = product["kubeconfig"]

    target_dir = os.path.join(
        must_gather_output_dir, "must-gather", cluster_name
    )

    try:
        if not os.path.exists(kubeconfig_path):
            LOGGER.error("Kubeconfig does not exist; cannot run must-gather.")
            return

        LOGGER.info(f"Prepare must-gather target extracted directory {target_dir}.")
        Path(target_dir).mkdir(parents=True, exist_ok=True)

        click.echo(
            f"Collect must-gather for cluster {cluster_name}"
        )
        run_must_gather(
            target_base_dir=target_dir,
            kubeconfig=kubeconfig_path,
        )
        LOGGER.success("must-gather collected")

    except Exception as ex:
        LOGGER.error(
            f"Failed to run must-gather \n{ex}",
        )

        LOGGER.info(f"Delete must-gather target directory {target_dir}.")
        shutil.rmtree(target_dir)
