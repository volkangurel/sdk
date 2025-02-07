from pathlib import Path
from typing import Optional
from unittest.mock import Mock, patch

from layerapi.api.entity.model_version_pb2 import ModelVersion
from layerapi.api.ids_pb2 import ModelTrainId
from yarl import URL

from layer.contracts.models import TrainedModelObject
from layer.contracts.runs import ResourceTransferState
from layer.flavors.base import ModelFlavor
from layer.flavors.model_definition import ModelDefinition


def test_model_load_from_cache(tmp_path: Path):
    model_train_id = ModelTrainId(value="a7c02598-7910-4f96-b8e6-bc6b62bd214f")
    model_def: ModelDefinition = Mock(name="model_definition")
    model_def.model_train_id = model_train_id
    s3_endpoint = URL("https://model.catalog")

    with patch("layer.utils.s3.S3Util.download_dir") as s3_download:
        model_flavor = DummyModelFlavor(cache_dir=tmp_path)
        s3_download.side_effect = download_model_files_from_s3
        model_flavor.load_from_s3(
            model_def, s3_endpoint_url=s3_endpoint, state=ResourceTransferState()
        )
        # assert model was downloaded and loaded from the cache dir
        model_cache_dir: Path = tmp_path / "cache" / model_train_id.value
        s3_download.assert_called_once()
        model_flavor.model_impl.assert_called_once_with(model_cache_dir.as_uri())
        assert not model_flavor.from_cache

    with patch("layer.utils.s3.S3Util.download_dir") as s3_download:
        model_flavor = DummyModelFlavor(cache_dir=tmp_path)
        model_flavor.load_from_s3(
            model_def, s3_endpoint_url=s3_endpoint, state=ResourceTransferState()
        )
        # assert model was loaded from the cache dir without downloading
        model_cache_dir: Path = tmp_path / "cache" / model_train_id.value
        s3_download.assert_not_called()
        model_flavor.model_impl.assert_called_once_with(model_cache_dir.as_uri())
        assert model_flavor.from_cache


def test_model_load_from_cache_does_not_cache_if_no_cache_true(tmp_path):
    model_train_id = ModelTrainId(value="0b4d4ef8-c81e-493c-bf61-9e437a8c6f6e")
    model_def: ModelDefinition = Mock(name="model_definition")
    model_def.model_train_id = model_train_id
    s3_endpoint = URL("https://model.catalog")

    with patch("layer.utils.s3.S3Util.download_dir") as s3_download:
        model_flavor = DummyModelFlavor(cache_dir=tmp_path, no_cache=True)
        s3_download.side_effect = download_model_files_from_s3
        model_flavor.load_from_s3(
            model_def, s3_endpoint_url=s3_endpoint, state=ResourceTransferState()
        )
        model_download_dir = s3_download.call_args[1]["local_dir"]
        s3_download.assert_called_once()
        model_flavor.model_impl.assert_called_once_with(model_download_dir.as_uri())
        assert not model_flavor.from_cache


def download_model_files_from_s3(*args, **kwargs):
    # mock the download from s3 by creating the download directory
    local_dir: Path = kwargs["local_dir"]
    local_dir.mkdir(parents=True, exist_ok=False)


class DummyModelFlavor(ModelFlavor):
    MODULE_KEYWORD = "dummy"
    PROTO_FLAVOR = ModelVersion.ModelFlavor.Value("MODEL_FLAVOR_INVALID")

    def __init__(
        self, no_cache: bool = False, cache_dir: Optional[Path] = None
    ) -> None:
        super().__init__(no_cache=no_cache, cache_dir=cache_dir)
        self.model_impl = Mock(name="dummy_model_impl")

    def save_model_to_directory(
        self, model_object: TrainedModelObject, directory: Path
    ) -> None:
        return

    def load_model_from_directory(self, directory: Path) -> TrainedModelObject:
        return self.model_impl(directory.as_uri())
