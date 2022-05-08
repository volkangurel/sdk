import logging
import warnings
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Union,
)
from uuid import UUID

from yarl import URL

from layer.cache import Cache
from layer.common import LayerClient
from layer.config import DEFAULT_PATH, DEFAULT_URL, ConfigManager
from layer.exceptions.exceptions import (
    ConfigError,
    ProjectInitializationException,
    UserConfigurationError,
    UserNotLoggedInException,
    UserWithoutAccountError,
)
from layer.fabric import Fabric
from layer.logged_data.log_data_runner import LogDataRunner
from layer.projects.init_project_runner import InitProjectRunner
from layer.projects.project import Project
from layer.projects.project_runner import ProjectRunner, Run
from layer.projects.tracker.remote_execution_project_progress_tracker import (
    RemoteExecutionProjectProgressTracker,
)
from layer.projects.util import get_current_project_name
from layer.settings import LayerSettings

from .async_utils import asyncio_run_in_thread
from .client import Dataset, Model
from .global_context import current_project_name, get_active_context
from .projects.asset import AssetPath, AssetType, parse_asset_path
from .train import Train


if TYPE_CHECKING:
    import matplotlib.figure  # type: ignore
    import pandas
    import PIL.Image  # type: ignore


logger = logging.getLogger(__name__)

warnings.filterwarnings(
    "ignore",
    "`should_run_async` will not call `transform_cell` automatically in the future.",
    DeprecationWarning,
)


def login(url: Union[URL, str] = DEFAULT_URL) -> None:
    """
    :param url: Not used.
    :raise UserNotLoggedInException: If the user is not logged in.
    :raise UserConfigurationError: If Layer user is not configured correctly.

    Logs user in to Layer. You might be prompted to enter an access token from a web page that is provided.

    .. code-block:: python

        layer.login()

    """

    async def _login(manager: ConfigManager, login_url: URL) -> None:
        await manager.login_headless(login_url)

    _refresh_and_login_if_needed(url, _login)


def login_with_access_token(
    access_token: str, url: Union[URL, str] = DEFAULT_URL
) -> None:
    """
    :param access_token: A valid access token retrieved from Layer.
    :param url: Not used.
    :raise UserNotLoggedInException: If the user is not logged in.
    :raise UserConfigurationError: If Layer user is not configured correctly.

    Log in with an access token. You might be prompted to enter an access token from a web page that is provided.

    .. code-block:: python

        layer.login_with_access_token(TOKEN)
    """

    async def _login(manager: ConfigManager, login_url: URL) -> None:
        await manager.login_with_access_token(login_url, access_token)

    _refresh_and_login_if_needed(url, _login)


def _refresh_and_login_if_needed(
    url: Union[URL, str], login_func: Callable[[ConfigManager, URL], Awaitable[None]]
) -> None:
    async def _login(login_url: URL) -> None:
        manager = ConfigManager(DEFAULT_PATH)
        try:
            config = await manager.refresh()
            if not config.is_guest and config.url == login_url:
                # no need to re-login
                return
        except (UserNotLoggedInException, UserConfigurationError):
            pass

        try:
            await login_func(manager, login_url)
        except UserWithoutAccountError as ex:
            raise SystemExit(ex)

    if isinstance(url, str):
        url = URL(url)

    asyncio_run_in_thread(_login(url))


def login_as_guest(url: Union[URL, str] = DEFAULT_URL) -> None:
    """
    :param url: (optional) The target platform where the user wants to log in.

    Logs user in to Layer as a guest user. Guest users can view public projects, but they cannot add, edit, or delete entities.

    .. code-block:: python

        layer.login_as_guest() # Uses default target platform URL

    """

    async def _login_as_guest(url: URL) -> None:
        manager = ConfigManager(DEFAULT_PATH)
        await manager.login_as_guest(url)

    if isinstance(url, str):
        url = URL(url)

    asyncio_run_in_thread(_login_as_guest(url))


def login_with_api_key(api_key: str, url: Union[URL, str] = DEFAULT_URL) -> None:
    async def _login(url: URL) -> None:
        manager = ConfigManager(DEFAULT_PATH)
        await manager.login_with_api_key(url, api_key)

    if isinstance(url, str):
        url = URL(url)

    asyncio_run_in_thread(_login(url))


def logout() -> None:
    """
    Log out of Layer.

    .. code-block:: python

        layer.logout()

    """
    asyncio_run_in_thread(ConfigManager(DEFAULT_PATH).logout())


def show_api_key() -> None:
    config = ConfigManager(DEFAULT_PATH).load()
    print(config.credentials.refresh_token)


def get_dataset(name: str, no_cache: bool = False) -> Dataset:
    """
    :param name: Name or path of the dataset.
    :param no_cache: if True, force dataset fetch from the remote location.
    :return: A dataset object defined in a Layer project.

    Retrieves a Layer dataset object from the Discover > Datasets tab.

    Guest users can use this function to access public datasets without logging in to Layer.

    By default, this function caches dataset contents locally when possible.

    .. code-block:: python

        # The simplest application of this method just returns a list of dataset information.
        layer.get_dataset("titanic")

        # This example gets the titanic dataset, turns it into a Pandas dataframe, and then displays it.
        dataset = layer.get_dataset("titanic")
        df = dataset.to_pandas()
        df.head()

        # You can also get datasets from other projects you have access to, including public projects.
        layer.get_dataset("the-project/datasets/titanic")
    """
    config = asyncio_run_in_thread(ConfigManager().refresh(allow_guest=True))
    asset_path = parse_asset_path(name, expected_asset_type=AssetType.DATASET)
    asset_path = _ensure_asset_path_has_project_name(asset_path)

    def fetch_dataset() -> "pandas.DataFrame":
        with LayerClient(config.client, logger).init() as client:
            return client.data_catalog.fetch_dataset(
                asset_path.path(), no_cache=no_cache
            )

    return Dataset(
        asset_path=asset_path,
        _pandas_df_factory=fetch_dataset,
    )


def get_model(name: str, no_cache: bool = False) -> Model:
    """
    :param name: Name or path of the model. You can pass additional parameters in the name to retrieve a specific version of the model with format: ``model_name:major_version.minor_version``
    :param no_cache: if True, force model fetch from the remote location.
    :return: The model object.

    Retrieves a Layer model object from the Discover > Models tab.

    Guest users can use this function to access public models without logging in to Layer.

    By default, this function caches models locally when possible.

    .. code-block:: python

        # Loads the default version of the model.
        layer.get_model("churn_model")

        # Loads the latest train of version 2.
        layer.get_model("churn_model:2")

        # Loads a specific train of the model version 2.
        layer.get_model("churn_model:2.12")

        # Load a model from a project you aren't logged in to.
        layer.get_model("the-project/models/churn_model")
    """
    config = asyncio_run_in_thread(ConfigManager().refresh(allow_guest=True))
    asset_path = parse_asset_path(name, expected_asset_type=AssetType.MODEL)
    asset_path = _ensure_asset_path_has_project_name(asset_path)

    with LayerClient(config.client, logger).init() as client:
        model_definition = client.model_catalog.load_model_definition(
            path=asset_path.path()
        )
        train_object = client.model_catalog.load_by_model_definition(
            model_definition, no_cache=no_cache
        )
        metrics = client.model_catalog.get_model_train_metrics(
            model_definition.model_train_id
        )
        parameters = client.model_catalog.get_model_train_parameters(
            model_definition.model_train_id
        )
        return Model(
            asset_path=asset_path,
            trained_model_object=train_object,
            metrics=metrics,
            parameters=parameters,
        )


def _ensure_asset_path_has_project_name(
    composite: AssetPath,
) -> AssetPath:
    if composite.has_project():
        return composite
    elif not current_project_name():
        raise ProjectInitializationException(
            "Please specify the project name globally with layer.init('project-name')"
            "or have it in the entity full name like 'the-project/models/the-model-name'"
        )

    composite = composite.with_project_name(str(current_project_name()))
    return composite


@contextmanager
def start_train(
    name: str, version: str, train_id: Optional[UUID] = None
) -> Iterator[Train]:
    """
    :param name: name of the model
    :param version: version number of the model
    :param train_id: id to start the training with

    Initiates a model training, generating and retaining model metadata on the backend

    Example usage:
    import layer

    with layer.start_train(name="model_name", version=2) as train:
        train.log_parameter("param_name", "param_val")
        train.register_input(x_train)
        train.register_output(y_train)
        trained_model = .....
        train.save_model(trained_model)

    """
    project_name = current_project_name()
    if not project_name:
        raise Exception("Missing current project name")
    config = asyncio_run_in_thread(ConfigManager().refresh())
    with LayerClient(config.client, logger).init() as client:
        train = Train(
            layer_client=client,
            name=name,
            version=version,
            train_id=train_id,
            project_name=project_name,
        )
        with train:
            yield train


def init(
    project_name: str,
    fabric: Optional[str] = None,
    pip_packages: Optional[List[str]] = None,
    pip_requirements_file: Optional[str] = None,
) -> Project:
    """
    :param project_name: Name of the project to be initialized.
    :param fabric: Default fabric to use for current project when running code in Layer's backend.
    :param pip_packages: List of packages to install in Layer backend when running code for this project.
    :param pip_requirements_file: File name with list of packages to install in Layer backend when running code for this project.
    :return: Project object.

    Initializes a project with given name both locally and remotely.

    If the project does not exist in the Layer backend, then it will be automatically created.
    Additional settings can be passed in as parameters: namely fabric and packages to install.
    These settings are only applied to runs initiated from the current local environment.

    Other users can set different settings for the same project in their own local environments
    via ``layer.init``, and those will not impact your runs.

    Note that only one of ``pip_packages`` or ``pip_requirements_file`` should be provided.

    .. code-block:: python

        # Initialize new project and create it in Layer backend if it does not exist
        project = layer.init("my_project_name", fabric="x-small")
    """
    if pip_packages and pip_requirements_file:
        raise ValueError(
            "either pip_requirements_file or pip_packages should be provided, not both"
        )
    init_project_runner = InitProjectRunner(project_name, logger=logger)
    fabric_to_set = Fabric(fabric) if fabric else None
    return init_project_runner.setup_project(
        fabric=fabric_to_set,
        pip_packages=pip_packages,
        pip_requirements_file=pip_requirements_file,
    )


def run(functions: List[Any], debug: bool = False) -> Optional[Run]:
    """
    :param functions: List of decorated functions to run in the Layer backend.
    :param debug: Stream logs to console from infra executing the project remotely.
    :return: An object with information on the run triggered, unless there is an initialization error.

    Runs the specified functions on Layer infrastructure.

    Running the project does the following:
    - Adds new versions of decorated functions to the Layer web UI.
    - Runs any logs and stores them.

    .. code-block:: python

        import pandas as pd
        from layer
        from layer.decorators import dataset

        # Define a new function for dataset generation
        @dataset("my_dataset_name")
        def create_my_dataset():
            data = [[1, "product1", 15], [2, "product2", 20], [3, "product3", 10]]
            dataframe = pd.DataFrame(data, columns=["Id", "Product", "Price"])
            return dataframe

        # Initialize current project locally and remotely (if it doesn't exist)
        project = layer.init("my_project_name", fabric="x-small")

        # Run the dataset generation code using Layer's backend compute resources
        run = layer.run([create_my_dataset])
        # run = layer.run([create_my_dataset], debug=True)  # Stream logs to console
    """
    _ensure_all_functions_are_decorated(functions)

    project_name = get_current_project_name()
    layer_config = asyncio_run_in_thread(ConfigManager().refresh())
    project_runner = ProjectRunner(
        config=layer_config,
        project_progress_tracker_factory=RemoteExecutionProjectProgressTracker,
    )
    project = project_runner.with_functions(project_name, functions)
    run_id = project_runner.run(project, from_decorators=True, debug=debug)

    _make_notebook_links_open_in_new_tab()

    return Run(project_name, UUID(run_id.value))


# Normally, Colab/IPython opens links as an IFrame. One can open them as new tabs through the right-click menu or using shift+click.
# However, we would like to change the default behavior to always open on a new page.
def _make_notebook_links_open_in_new_tab() -> None:
    def is_notebook() -> bool:
        try:
            shell = get_ipython().__class__.__name__  # type: ignore
            if shell == "ZMQInteractiveShell":
                return True  # Jupyter notebook or qtconsole
            elif shell == "TerminalInteractiveShell":
                return False  # Terminal running IPython
            else:
                return False  # Other type (?)
        except NameError:
            return False  # Probably standard Python interpreter

    if is_notebook():
        from IPython.display import Javascript, display  # type: ignore

        # Colab
        display(
            Javascript(
                """
document.querySelectorAll("#output-area a").forEach(function(a){
    a.setAttribute('target', '_blank');
})
        """
            )
        )

        # Jupyter
        display(
            Javascript(
                """
document.querySelectorAll(".output a").forEach(function(a){
    a.setAttribute('target', '_blank');
})
        """
            )
        )


def _ensure_all_functions_are_decorated(functions: List[Callable[..., Any]]) -> None:
    for f in functions:
        if not hasattr(f, "layer"):
            raise ConfigError(
                'Either @dataset(name="...") or @model(name="...") is required for each function. '
                "Add @dataset or @model decorator to your functions to run them in Layer."
            )
        settings: LayerSettings = f.layer  # type: ignore
        settings.validate()


def log(
    data: Dict[
        str,
        Union[
            str,
            float,
            bool,
            int,
            "pandas.DataFrame",
            "PIL.Image.Image",
            "matplotlib.figure.Figure",
            ModuleType,
            Path,
        ],
    ],
    step: Optional[int] = None,
) -> None:
    """
    :param data: A dictionary in which each key is a string tag (i.e. name/id). The value can have different types. See examples below for more details.
    :param step: An optional integer that associates data with a particular step (epoch). This only takes effect it the logged data is to be associated with a model train (and *not* with a dataset build) and the data is a number.
    :return: None

    Logs arbitrary data associated with a model train or a dataset build into Layer backend.

    This function can only be run inside functions decorated with ``@model`` or ``@dataset``. The logged data can then be discovered and analyzed through the Layer UI.

    We support Python primitives, images, tables and plots to enable better experiment tracking and version comparison.

    **Python Primitives**

    You can log Python primitive types for your model train parameters, metrics or KPIs

    Accepted Types:
    ``str``,
    ``float``,
    ``bool``,
    ``int``

    **Images**

    You can log images to track inputs, outputs, detections, activations and more. We support JPEG and PNG formats.

    Accepted Types:
    ``PIL.Image.Image``,
    ``path.Path``

    **Charts**

    You can track your metrics in detail with charts

    Accepted Types:
    ``matplotlib.figure.Figure``,
    ``matplotlib.pyplot``,
    ``ModuleType`` (only for the matplotlib module, for convenience)

    **Tables**

    You can log dataframes to display and analyze your tabular data.

    Accepted Types:
    ``pandas.DataFrame``

    .. code-block:: python

        import matplotlib.pyplot as plt
        import pandas as pd
        from layer
        from layer.decorators import dataset, model

        # Define a new function for dataset generation
        @dataset("my_dataset_name")
        def create_my_dataset():
            data = [[1, "product1", 15], [2, "product2", 20], [3, "product3", 10]]
            dataframe = pd.DataFrame(data, columns=["Id", "Product", "Price"])

            t = np.arange(0.0, 2.0, 0.01)
            s = 1 + np.sin(2 * np.pi * t)
            fig, ax = plt.subplots()

            layer.log({
                "my-str-tag": "any text",
                "my-int-tag": 123, # any number
                "foo-bool": True,
                "some-sample-dataframe-tag": ..., # Pandas data frame
                "some-local-image-file: Path.home() / "images/foo.png",
                "some-matplot-lib-figure": fig, # You could alternatively just passed plt as well, and Layer would just get the current/active figure.
            })

            layer.log({
                "another-tag": "you can call layer.log multiple times"
            })

            return dataframe

        @model("my_model")
        def create_my_model():
            # everything that can be logged for a dataset (as shown above), can also be logged for a model too.

            # In addition to those, if you have a multi-step (i.e. multi-epoch) algorithm, you can associate a metric with the step.
            # These will be rendered on a graph inside the Layer UI.
            for i in range(1000):
                some_result, accuracy = some_algo(some_result)
                layer.log({
                    "Model accuracy": accuracy,
                }, step=i)

        create_my_dataset()
        create_my_model()
    """
    active_context = get_active_context()
    if not active_context:
        raise RuntimeError(
            "Data logging only allowed inside functions decorated with @model or @dataset"
        )
    train = active_context.train()
    train_id = train.get_id() if train is not None else None
    dataset_build = active_context.dataset_build()
    dataset_build_id = dataset_build.get_id() if dataset_build is not None else None
    layer_config = asyncio_run_in_thread(ConfigManager().refresh())
    with LayerClient(layer_config.client, logger).init() as client:
        log_data_runner = LogDataRunner(
            client=client,
            train_id=train_id,
            dataset_build_id=dataset_build_id,
            logger=logger,
        )
        log_data_runner.log(data=data, epoch=step)


def clear_cache() -> None:
    """
    Clear all cached objects fetched by the Layer SDK on this machine.

    The Layer SDK locally stores all datasets and models by default on your computer.
    When you fetch a dataset with :func:``layer.get_dataset``, or load the model with ``layer.get_model``,
    the first call will fetch the artifact from remote storage,
    but subsequent calls will read it from the local disk.
    """
    Cache().clear()
