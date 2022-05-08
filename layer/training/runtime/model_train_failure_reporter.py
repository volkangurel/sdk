from logging import Logger
from pathlib import Path
from uuid import UUID

from layer.api.entity.model_train_status_pb2 import ModelTrainStatus
from layer.api.ids_pb2 import ModelTrainId
from layer.client import ModelCatalogClient
from layer.exceptions.status_report import (
    ExecutionStatusReportFactory,
    PythonExecutionStatusReport,
)
from layer.training.runtime.common import update_train_status


class ModelTrainFailureReporter:
    model_catalog_client: ModelCatalogClient
    logger: Logger
    train_id: UUID
    source_folder: Path

    def __init__(
        self,
        model_catalog_client: ModelCatalogClient,
        logger: Logger,
        train_id: UUID,
        source_folder: Path,
    ) -> None:
        self.model_catalog_client = model_catalog_client
        self.logger = logger
        self.train_id = train_id
        self.source_folder = source_folder

    def report_failure(self, stage: str, failure_exc: Exception) -> None:
        # Check to only keep cause for inner most exception, as __exit__
        # catches SystemExit exceptions, thus without this if a chain of status updates
        # could be triggered with the outer most exception message overriding inner ones
        existing_status = self._get_train_status()
        if existing_status != ModelTrainStatus.TRAIN_STATUS_FAILED:
            update_train_status(
                self.model_catalog_client,
                self.train_id,
                ModelTrainStatus.TRAIN_STATUS_FAILED,
                self.logger,
                info=ExecutionStatusReportFactory.to_json(
                    PythonExecutionStatusReport.from_exception(
                        failure_exc, self.source_folder
                    )
                ),
            )

    def _get_train_status(self) -> "ModelTrainStatus.TrainStatus.V":
        return self.model_catalog_client.get_model_train(
            ModelTrainId(value=str(self.train_id))
        ).train_status.train_status
