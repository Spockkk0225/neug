

/** Copyright 2020 Alibaba Group Holding Limited.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * 	http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "neug/execution/execute/ops/retrieve/scan_utils.h"
#include "neug/common/types/value.h"
#include "neug/execution/utils/pb_parse_utils.h"
#include "neug/utils/exception/exception.h"

namespace neug {
namespace execution {
namespace ops {

static std::vector<const common::Value*> parse_collection_expression(
    const common::Expression& expression) {
  std::vector<const common::Value*> values;
  if (expression.operators_size() != 1) {
    return values;
  }
  const auto& opr = expression.operators(0);
  if (!opr.has_to_list() && !opr.has_to_array()) {
    THROW_NOT_SUPPORTED_EXCEPTION(
        "unsupported expression in primary key index predicate");
  }
  const auto& fields =
      opr.has_to_list() ? opr.to_list().fields() : opr.to_array().fields();
  for (const auto& field : fields) {
    if (field.operators_size() != 1 || !field.operators(0).has_const_()) {
      THROW_NOT_SUPPORTED_EXCEPTION(
          "primary key collection elements must be constants");
    }
    values.emplace_back(&field.operators(0).const_());
  }
  return values;
}

template <typename T>
std::vector<Value> parse_ids_from_idx_predicate(
    const algebra::IndexPredicate_Triplet& triplet, const ParamsMap& params) {
  const auto& expression = triplet.expression();
  if (expression.operators_size() == 0) {
    return {};
  }
  const auto& opr = expression.operators(0);
  if (opr.has_const_()) {
    std::vector<Value> ret;
    if (opr.const_().item_case() == common::Value::kI32) {
      ret.emplace_back(
          Value::CreateValue<T>(static_cast<T>(opr.const_().i32())));
    } else if (opr.const_().item_case() == common::Value::kI64) {
      ret.emplace_back(
          Value::CreateValue<T>(static_cast<T>(opr.const_().i64())));
    } else if (opr.const_().item_case() == common::Value::kU32) {
      ret.emplace_back(
          Value::CreateValue<T>(static_cast<T>(opr.const_().u32())));
    } else if (opr.const_().item_case() == common::Value::kU64) {
      ret.emplace_back(
          Value::CreateValue<T>(static_cast<T>(opr.const_().u64())));
    }
    return ret;
  }
  if (opr.has_param()) {
    auto param_type = parse_from_ir_data_type(opr.param().data_type());

    if (param_type.id() == DataTypeId::kInt32) {
      return std::vector<Value>{Value::CreateValue<T>(
          params.at(opr.param().name()).template GetValue<T>())};
    } else if (param_type.id() == DataTypeId::kInt64) {
      return std::vector<Value>{Value::CreateValue<T>(
          params.at(opr.param().name()).template GetValue<T>())};
    }
  }
  if (opr.has_to_list() || opr.has_to_array()) {
    std::vector<Value> ret;
    for (const auto* value : parse_collection_expression(expression)) {
      if (value->item_case() == common::Value::kI32) {
        ret.emplace_back(Value::CreateValue<T>(static_cast<T>(value->i32())));
      } else if (value->item_case() == common::Value::kI64) {
        ret.emplace_back(Value::CreateValue<T>(static_cast<T>(value->i64())));
      } else if (value->item_case() == common::Value::kU32) {
        ret.emplace_back(Value::CreateValue<T>(static_cast<T>(value->u32())));
      } else if (value->item_case() == common::Value::kU64) {
        ret.emplace_back(Value::CreateValue<T>(static_cast<T>(value->u64())));
      }
    }
    return ret;
  }
  return {};
}

std::vector<Value> parse_ids_from_idx_predicate(
    const algebra::IndexPredicate_Triplet& triplet, const ParamsMap& params) {
  std::vector<Value> ret;
  const auto& expression = triplet.expression();
  if (expression.operators_size() == 0) {
    return ret;
  }
  const auto& opr = expression.operators(0);
  if (opr.has_const_()) {
    if (opr.const_().item_case() == common::Value::kStr) {
      ret.emplace_back(Value::STRING(opr.const_().str()));
    }
    return ret;
  }
  if (opr.has_param()) {
    auto param_type = parse_from_ir_data_type(opr.param().data_type());

    if (param_type.id() == DataTypeId::kVarchar) {
      ret.emplace_back(params.at(opr.param().name()));
      return ret;
    }
  }
  if (opr.has_to_list() || opr.has_to_array()) {
    for (const auto* value : parse_collection_expression(expression)) {
      if (value->item_case() == common::Value::kStr) {
        ret.emplace_back(Value::STRING(value->str()));
      }
    }
    return ret;
  }
  return ret;
}

static const std::vector<Value>* get_pk_collection_param(
    const algebra::IndexPredicate_Triplet& triplet, const ParamsMap& params) {
  if (!triplet.has_expression() ||
      triplet.expression().operators_size() != 1 ||
      !triplet.expression().operators(0).has_param()) {
    return nullptr;
  }
  const auto& param = triplet.expression().operators(0).param();
  const auto& value = params.at(param.name());
  const auto type = value.type().id();
  if (type == DataTypeId::kList) {
    return &ListValue::GetChildren(value);
  }
  if (type == DataTypeId::kArray) {
    return &ArrayValue::GetChildren(value);
  }
  return nullptr;
}

std::vector<Value> ScanUtils::parse_ids_with_type(
    DataTypeId type, const algebra::IndexPredicate_Triplet& triplet,
    const ParamsMap& params) {
  if (auto collectionValues = get_pk_collection_param(triplet, params)) {
    return *collectionValues;
  }
  switch (type) {
  case DataTypeId::kInt64: {
    return parse_ids_from_idx_predicate<int64_t>(triplet, params);
  }
  case DataTypeId::kInt32: {
    return parse_ids_from_idx_predicate<int32_t>(triplet, params);
  }
  case DataTypeId::kUInt64: {
    return parse_ids_from_idx_predicate<uint64_t>(triplet, params);
  }
  case DataTypeId::kUInt32: {
    return parse_ids_from_idx_predicate<uint32_t>(triplet, params);
  }
  case DataTypeId::kVarchar: {
    return parse_ids_from_idx_predicate(triplet, params);
  }
  default:
    THROW_NOT_SUPPORTED_EXCEPTION("unsupported type" +
                                  std::to_string(static_cast<int>(type)));
    break;
  }
  return {};
}
bool ScanUtils::check_idx_predicate(const physical::Scan& scan_opr) {
  if (scan_opr.scan_opt() != physical::Scan::VERTEX) {
    return false;
  }

  if (!scan_opr.has_params()) {
    return false;
  }

  if (!scan_opr.has_idx_predicate()) {
    return false;
  }
  const algebra::IndexPredicate& predicate = scan_opr.idx_predicate();
  if (predicate.or_predicates_size() != 1) {
    return false;
  }
  if (predicate.or_predicates(0).predicates_size() != 1) {
    return false;
  }
  const algebra::IndexPredicate_Triplet& triplet =
      predicate.or_predicates(0).predicates(0);
  if (!triplet.has_key()) {
    return false;
  }

  if (triplet.cmp() != common::Logical::EQ &&
      triplet.cmp() != common::Logical::WITHIN) {
    return false;
  }

  if (!triplet.has_expression() ||
      triplet.expression().operators_size() == 0) {
    return false;
  }

  return true;
}

}  // namespace ops
}  // namespace execution
}  // namespace neug
