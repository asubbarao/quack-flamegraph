use crate::folded::{load_folded_file, FoldedStack};
use duckdb::{
    core::{DataChunkHandle, Inserter, LogicalTypeHandle, LogicalTypeId},
    vtab::{BindInfo, InitInfo, TableFunctionInfo, VTab},
};
use std::{
    error::Error,
    sync::atomic::{AtomicUsize, Ordering},
};

#[repr(C)]
pub struct FoldedBindData {
    stacks: Vec<FoldedStack>,
    source: String,
}

#[repr(C)]
pub struct FoldedInitData {
    cursor: AtomicUsize,
}

pub struct ReadFoldedVTab;

impl VTab for ReadFoldedVTab {
    type BindData = FoldedBindData;
    type InitData = FoldedInitData;

    fn bind(bind: &BindInfo) -> Result<Self::BindData, Box<dyn Error>> {
        let varchar = LogicalTypeHandle::from(LogicalTypeId::Varchar);
        bind.add_result_column("frames", LogicalTypeHandle::list(&varchar));
        bind.add_result_column("samples", LogicalTypeId::Bigint.into());
        bind.add_result_column("source", LogicalTypeId::Varchar.into());

        let path = bind.get_parameter(0).to_string();
        let stacks = load_folded_file(&path)?;
        Ok(FoldedBindData {
            stacks,
            source: path,
        })
    }

    fn init(_: &InitInfo) -> Result<Self::InitData, Box<dyn Error>> {
        Ok(FoldedInitData {
            cursor: AtomicUsize::new(0),
        })
    }

    fn func(
        func: &TableFunctionInfo<Self>,
        output: &mut DataChunkHandle,
    ) -> Result<(), Box<dyn Error>> {
        let bind = func.get_bind_data();
        let cursor = &func.get_init_data().cursor;
        let capacity = output.flat_vector(1).capacity();
        let start = cursor.fetch_add(capacity, Ordering::Relaxed);
        let total = bind.stacks.len();
        if start >= total {
            output.set_len(0);
            return Ok(());
        }
        let rows = capacity.min(total - start);
        let batch = &bind.stacks[start..start + rows];

        let child_count: usize = batch.iter().map(|stack| stack.frames.len()).sum();
        {
            let mut list = output.list_vector(0);
            let child = list.child(child_count.max(1));
            let mut offset = 0usize;
            for (i, stack) in batch.iter().enumerate() {
                list.set_entry(i, offset, stack.frames.len());
                for frame in &stack.frames {
                    child.insert(offset, frame.as_str());
                    offset += 1;
                }
            }
            list.set_len(offset);
        }

        {
            let mut samples = output.flat_vector(1);
            let slice = unsafe { samples.as_mut_slice_with_len::<i64>(rows) };
            for (i, stack) in batch.iter().enumerate() {
                slice[i] = stack.samples;
            }
        }

        {
            let source = output.flat_vector(2);
            for i in 0..rows {
                source.insert(i, bind.source.as_str());
            }
        }

        output.set_len(rows);
        Ok(())
    }

    fn parameters() -> Option<Vec<LogicalTypeHandle>> {
        Some(vec![LogicalTypeHandle::from(LogicalTypeId::Varchar)])
    }
}
